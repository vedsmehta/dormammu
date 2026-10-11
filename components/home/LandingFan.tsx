"use client";

import { useState, type CSSProperties } from "react";

/*
  The landing hero's ask box and the "one request, six calls" fan from frontend/index.html and
  frontend/ask.js. Chips fill the box and swap the example fan-out; "Handle it" goes to sign in.
  Everything here is example data, as the note under it says.
*/

type End = [state: string, vendor: string, got: string, was: string, note: string];

const SCENES: Record<string, End[]> = {
  move: [
    ["", "Sutro Fiber", "$50/mo", "$70", "Best internet quote"],
    ["", "BayLink Internet", "$55/mo", "$65", "Done"],
    ["texting", "Cableton", "Texting", "", "No answer, so it switched to text"],
    ["talking", "Hearth Mutual", "Talking", "", "Asking for the student rate"],
    ["ringing", "Lantern Insurance", "Ringing", "", "Renters insurance"],
    ["", "Campus Dental", "Thu 2:30", "", "Cleaning booked"],
  ],
  bill: [
    ["talking", "Northline retention", "Talking", "", "Asking them to keep your rate"],
    ["ringing", "Northline loyalty", "Ringing", "", "A second try, different desk"],
    ["", "Pebble Mobile", "$40/mo", "$50", "Competing quote to compare"],
    ["", "Tandem Wireless", "$48/mo", "$55", "Competing quote to compare"],
    ["texting", "Kite Mobile", "Texting", "", "No answer, so it switched to text"],
    ["", "Orbit Cellular", "$52/mo", "", "No student rate"],
  ],
  dentist: [
    ["", "Campus Dental", "Thu 2:30", "", "Earliest slot, booked"],
    ["", "Elm Street Dental", "Fri 9:00", "", "Open, not booked"],
    ["", "Bright Smile", "Full", "", "Nothing in the next two weeks"],
    ["talking", "Parkside Dental", "Talking", "", "Checking Tuesday afternoon"],
    ["ringing", "University Dental", "Ringing", "", ""],
    ["texting", "Dr. Okafor", "Texting", "", "Books by text"],
  ],
  cancel: [
    ["", "Ironworks Gym", "Canceled", "", "Last charge on the 1st"],
    ["", "StreamBox", "Canceled", "", "Confirmation on file"],
    ["talking", "MealCrate", "Talking", "", "Declining the retention offer"],
    ["ringing", "CloudVault", "Ringing", "", ""],
    ["ringing", "BoxFit", "Needs you", "", "They want the account holder"],
    ["texting", "Paperback Club", "Texting", "", "No answer, so it switched to text"],
  ],
};

const CHIPS: { scene: keyof typeof SCENES; label: string; fill: string }[] = [
  { scene: "move", label: "Moving in", fill: "I'm moving into my first apartment near campus on the 1st, handle it." },
  { scene: "bill", label: "Lower a bill", fill: "My phone bill is too high, get it down." },
  { scene: "dentist", label: "Book a dentist", fill: "Find me a dentist near campus this week." },
  { scene: "cancel", label: "Cancel something", fill: "Cancel the subscriptions I don't use." },
];

const PATHS = [
  "M500 0 C500 150 83 90 83 250",
  "M500 0 C500 150 250 90 250 250",
  "M500 0 C500 150 417 90 417 250",
  "M500 0 C500 150 583 90 583 250",
  "M500 0 C500 150 750 90 750 250",
  "M500 0 C500 150 917 90 917 250",
];
const BEGINS = ["-0.3s", "-1.9s", "-1.1s", "-2.4s", "-0.7s", "-1.5s"];

export function LandingFan() {
  const [scene, setScene] = useState<keyof typeof SCENES>("move");
  const [text, setText] = useState(CHIPS[0].fill);
  // Bumps whenever a chip swaps the scene so the lines draw in again, without the page-load delay.
  const [swaps, setSwaps] = useState(0);

  const pick = (c: (typeof CHIPS)[number]) => {
    setText(c.fill);
    setScene(c.scene);
    setSwaps((n) => n + 1);
  };

  const fanStyle = swaps > 0 ? ({ "--t0": "0s" } as CSSProperties) : undefined;

  return (
    <div className="fan" style={fanStyle}>
      <form className="askbox rise" style={{ "--d": ".4s" } as CSSProperties} action="/login">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="What do you need handled?"
          placeholder="What do you need handled?"
          autoComplete="off"
        />
        <button type="submit" className="btn btn-primary">
          Handle it
        </button>
      </form>
      <div className="chips rise" style={{ "--d": ".5s" } as CSSProperties} aria-label="Try an example">
        {CHIPS.map((c) => (
          <button key={c.scene} type="button" className="glass-pill" aria-pressed={scene === c.scene} onClick={() => pick(c)}>
            {c.label}
          </button>
        ))}
      </div>

      <svg key={swaps} className="fan-lines" viewBox="0 0 1000 250" aria-hidden="true">
        <defs>
          <linearGradient id="fan-stroke" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="250">
            <stop offset="0" stopColor="#fff" />
            <stop offset="1" stopColor="#86f7e2" />
          </linearGradient>
        </defs>
        {PATHS.map((d, i) => (
          <path key={i} id={`f${i}`} style={{ "--i": i } as CSSProperties} pathLength={1} d={d} />
        ))}
        {PATHS.map((_, i) => (
          <circle key={i} r="3.5">
            <animateMotion dur="2.8s" begin={BEGINS[i]} repeatCount="indefinite">
              <mpath href={`#f${i}`} />
            </animateMotion>
          </circle>
        ))}
      </svg>

      <ol key={`ends-${swaps}`} className="ends" aria-label="Example: six calls placed at once" aria-live="polite">
        {SCENES[scene].map(([state, vendor, got, was, note], i) => (
          <li key={vendor} style={{ "--i": i } as CSSProperties}>
            <span className={state ? `dot ${state}` : "dot"} />
            <b>{vendor}</b>
            <span className={state ? `got t-${state}` : "got"}>
              {was ? <s>{was}</s> : null}
              {got}
            </span>
            {note ? <small>{note}</small> : null}
          </li>
        ))}
      </ol>
      <p className="fan-note">Example runs. Vendors and prices are made up. “Handle it” takes you to sign in.</p>
    </div>
  );
}
