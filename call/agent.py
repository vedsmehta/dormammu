"""LiveKit voice agent that places one outbound call per job over a SIP trunk.

uv run python -m call.agent download-files   # once, fetches the VAD model
uv run python -m call.agent dev               # run the worker locally
"""

import asyncio
import json
import logging
import time

from livekit import api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    inference,
)
from livekit.agents.beta.tools import EndCallTool, send_dtmf_events
from livekit.plugins import silero

from call.models import CallRequest, CallResult, write_result
from settings import env, load_env

load_env()
logger = logging.getLogger("dormammu.call")

AGENT_NAME = "dormammu-caller"
CALLEE_IDENTITY = "callee"

INSTRUCTIONS = """You are {caller_name}, placing a phone call on behalf of a client.

Your task:
{task}

How to behave:
- You are speaking out loud on a phone line. Use short, natural sentences. No lists, markdown or emojis.
- Wait for the other side to speak first, then introduce yourself and get to the point.
- If you reach an automated phone menu, listen to every option, then use send_dtmf_events to press
  the keys that get you to a human or the right department.
- If you reach voicemail, end the call unless your task says to leave a message.
- Never invent facts about the client. If asked for something you don't know, say you'll follow up.
- Be polite but persistent. If they can't help, ask who can and how to reach them.
- Once you have what you need, or it's clear you won't get it, call record_outcome, then end the call.
{fields}"""


def build_instructions(request: CallRequest) -> str:
    fields = ""
    if request.fields:
        fields = "\nInformation to collect (use these exact keys in record_outcome): " + ", ".join(
            request.fields
        )
    return INSTRUCTIONS.format(caller_name=request.caller_name, task=request.task, fields=fields)


class Caller(Agent):
    def __init__(self, request: CallRequest, result: CallResult) -> None:
        super().__init__(
            instructions=build_instructions(request),
            tools=[EndCallTool(), send_dtmf_events],
        )
        self._result = result

    @function_tool
    async def record_outcome(
        self, context: RunContext, success: bool, summary: str, details_json: str = "{}"
    ) -> str:
        """Record what happened on this call. Call this once, right before ending the call.

        Args:
            success: Whether the task was accomplished.
            summary: One or two sentences on what was learned or agreed.
            details_json: A JSON object of the collected information, e.g. {"price": "$1200"}.
        """
        try:
            details = json.loads(details_json)
        except json.JSONDecodeError:
            details = {"raw": details_json}
        self._result.success = success
        self._result.summary = summary
        self._result.details = {str(k): str(v) for k, v in details.items()}
        self._result.status = "completed"
        return "Outcome recorded. Wrap up and end the call."


def transcript(session: AgentSession) -> list[dict[str, str]]:
    return [
        {"role": item.role, "text": item.text_content}
        for item in session.history.items
        if item.type == "message" and item.text_content
    ]


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    request = CallRequest.from_json(ctx.job.metadata)
    result = CallResult(room=ctx.room.name, to=request.to)
    started = time.monotonic()

    session = AgentSession(
        stt=env("CALL_STT", "deepgram/nova-3"),
        llm=env("CALL_LLM", "openai/gpt-4.1-mini"),
        tts=env("CALL_TTS", "cartesia/sonic-3"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=inference.TurnDetector(),
        ivr_detection=True,
    )

    async def finish(_: str = "") -> None:
        result.transcript = transcript(session)
        result.duration_seconds = round(time.monotonic() - started, 1)
        write_result(result)

    ctx.add_shutdown_callback(finish)
    session.on("close", lambda _: ctx.shutdown("session closed"))

    await ctx.connect()
    session_started = asyncio.create_task(session.start(agent=Caller(request, result), room=ctx.room))

    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=env("LIVEKIT_SIP_OUTBOUND_TRUNK_ID"),
                sip_call_to=request.to,
                participant_identity=CALLEE_IDENTITY,
                wait_until_answered=True,
            )
        )
    except api.TwirpError as e:
        sip_status = e.metadata.get("sip_status_code", "")
        result.status = "no_answer" if sip_status in {"408", "480", "486", "603"} else "failed"
        result.error = f"{e.message} (sip {sip_status})" if sip_status else e.message
        logger.warning("call to %s not connected: %s", request.to, result.error)
        session_started.cancel()
        ctx.shutdown("call not connected")
        return

    await session_started
    await ctx.wait_for_participant(identity=CALLEE_IDENTITY)
    result.status = "answered"


if __name__ == "__main__":
    cli.run_app(server)
