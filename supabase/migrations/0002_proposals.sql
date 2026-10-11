-- Research-first flow (docs/CONTRACT.md "Proposals"). A proposal is what the research engine found for one
-- request: providers, plans, discounts, verified phones, a strategy per item and the regulations it checked.
-- The user approves items; approval creates a run. Vendors learn where a row came from (manual onboarding or
-- research) and keep the website the phone was verified against.

create table if not exists proposals (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references profiles(id) on delete cascade,
  request           text not null,
  status            text not null default 'proposed'
                      check (status in ('researching','proposed','approved','dispatched','rejected','failed')),
  proposal          jsonb not null default '{}',
  approved_item_ids jsonb not null default '[]',
  run_id            uuid references runs(id) on delete set null,
  created_at        timestamptz default now(),
  updated_at        timestamptz default now()
);
create index if not exists proposals_user_created_idx on proposals (user_id, created_at desc);

alter table proposals enable row level security;
drop policy if exists "own proposals" on proposals;
create policy "own proposals" on proposals for all using (user_id = auth.uid());

-- vendors: where the row came from, and the page the phone was verified against (research rows only).
alter table vendors add column if not exists source  text not null default 'manual';
alter table vendors add column if not exists website text;
