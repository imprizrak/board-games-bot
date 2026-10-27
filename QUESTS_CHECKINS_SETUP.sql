-- Styloteka v5.2: organizer-only attendance for Quests Inc public participants.
-- No names or profile URLs are persisted, only a one-way profile identifier.
create table if not exists public.event_quests_checkins (
  event_id bigint not null references public.events(id) on delete cascade,
  quest_id uuid not null,
  participant_key text not null check (participant_key ~ '^[a-f0-9]{64}$'),
  checked_in_at timestamptz not null default now(),
  checked_in_by bigint not null,
  primary key (event_id, quest_id, participant_key)
);

alter table public.event_quests_checkins enable row level security;
revoke all on table public.event_quests_checkins from public, anon, authenticated;
grant select, insert, update, delete on public.event_quests_checkins to service_role;
