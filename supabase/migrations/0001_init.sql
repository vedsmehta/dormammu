-- Dormammu schema. BUILD-PLAN.md §3 plus §12 (ledger, cohort). RLS on from the first line.

create extension if not exists pgcrypto;

-- profiles: one row per authenticated user
create table if not exists profiles (
  id           uuid primary key references auth.users on delete cascade,
  display_name text,
  phone        text,
  prefs        jsonb not null default '{}',
  cohort       text not null default 'student',
  created_at   timestamptz not null default now()
);

-- vendors: the call roster. Phone numbers live here, never in git.
create table if not exists vendors (
  id        uuid primary key default gen_random_uuid(),
  owner_id  uuid not null references profiles(id) on delete cascade,
  key       text not null,
  name      text not null,
  track     text not null check (track in ('quote','schedule','negotiate')),
  phone     text not null,
  is_demo   boolean not null default false,
  unique (owner_id, key)
);

create table if not exists runs (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references profiles(id) on delete cascade,
  request      text not null,
  status       text not null default 'dispatching'
                 check (status in ('dispatching','running','complete','failed')),
  created_at   timestamptz not null default now(),
  completed_at timestamptz
);

create table if not exists calls (
  id               uuid primary key default gen_random_uuid(),
  run_id           uuid not null references runs(id) on delete cascade,
  vendor_id        uuid references vendors(id) on delete set null,
  vendor_key       text,
  track            text not null,
  target_name      text not null,
  target_phone     text not null,
  objective        text not null,
  prior_context    text,
  dial_call_id     text unique,
  state            text not null default 'planned',
  state_rank       int  not null default 0,
  termination_type text,
  transcript       text,
  transcript_turns jsonb,
  outcome          jsonb,
  simulated        boolean not null default false,
  error            text,
  duration_seconds double precision,
  started_at       timestamptz not null default now(),
  ended_at         timestamptz
);
create index if not exists calls_run_id_idx on calls (run_id);
create index if not exists calls_dial_call_id_idx on calls (dial_call_id);
create index if not exists calls_vendor_key_idx on calls (vendor_key);

-- events: the system of record
create table if not exists events (
  id         bigserial primary key,
  user_id    uuid not null references profiles(id) on delete cascade,
  run_id     uuid references runs(id) on delete cascade,
  call_id    uuid references calls(id) on delete cascade,
  type       text not null,
  data       jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index if not exists events_user_created_idx on events (user_id, created_at desc);
create index if not exists events_call_id_idx on events (call_id);

-- plan_cache: keeps the demo's first beat instant
create table if not exists plan_cache (
  request_hash text primary key,
  plans        jsonb not null,
  created_at   timestamptz not null default now()
);

-- ledger: dated obligations, the reason the product exists between runs
create table if not exists ledger (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references profiles(id) on delete cascade,
  vendor_id    uuid references vendors(id) on delete set null,
  kind         text not null check (kind in ('promo_expiry','renewal','appointment','quote')),
  label        text not null,
  amount_usd   numeric,
  effective_on date,
  expires_on   date,
  source_call  uuid references calls(id) on delete set null,
  created_at   timestamptz not null default now()
);
create index if not exists ledger_user_expires_idx on ledger (user_id, expires_on);

-- Row Level Security
alter table profiles   enable row level security;
alter table vendors    enable row level security;
alter table runs       enable row level security;
alter table calls      enable row level security;
alter table events     enable row level security;
alter table plan_cache enable row level security;   -- no policy: service role only
alter table ledger     enable row level security;

drop policy if exists "own profile" on profiles;
create policy "own profile" on profiles for all using (id = auth.uid());

drop policy if exists "own vendors" on vendors;
create policy "own vendors" on vendors for all using (owner_id = auth.uid());

drop policy if exists "own runs" on runs;
create policy "own runs" on runs for all using (user_id = auth.uid());

drop policy if exists "own calls" on calls;
create policy "own calls" on calls for all using (exists (
  select 1 from runs where runs.id = calls.run_id and runs.user_id = auth.uid()));

drop policy if exists "own events" on events;
create policy "own events" on events for select using (user_id = auth.uid());

drop policy if exists "own ledger" on ledger;
create policy "own ledger" on ledger for all using (user_id = auth.uid());

-- profiles row on signup
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', split_part(new.email, '@', 1)))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- Realtime pushes row updates to the dashboard
do $$
begin
  alter publication supabase_realtime add table calls;
exception when duplicate_object then null;
end $$;
do $$
begin
  alter publication supabase_realtime add table runs;
exception when duplicate_object then null;
end $$;
