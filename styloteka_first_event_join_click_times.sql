-- Styloteka 5.3.41: only the authenticated Flask backend writes the first click.
-- Applied to production Supabase on 2026-11-22; included for reference.
CREATE TABLE IF NOT EXISTS public.event_join_intents (
  event_id bigint NOT NULL REFERENCES public.events(id) ON DELETE CASCADE,
  telegram_user_id bigint NOT NULL CHECK (telegram_user_id > 0),
  clicked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, telegram_user_id)
);
COMMENT ON TABLE public.event_join_intents IS 'First time a verified Telegram member presses Я йду on a receipt-paid event. Only backend service key may read/write.';
ALTER TABLE public.event_join_intents ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.event_join_intents FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON TABLE public.event_join_intents TO service_role;
