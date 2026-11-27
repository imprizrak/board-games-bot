create table if not exists public.styloteka_ai_memory (id text primary key, state jsonb not null);
alter table public.styloteka_ai_memory enable row level security;
revoke all on public.styloteka_ai_memory from anon, authenticated;
grant select, insert, update on public.styloteka_ai_memory to service_role;
