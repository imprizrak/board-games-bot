-- Upgrade ONLY if v5.3.19 receipts support is already installed.
-- For fresh setup, EVENT_RECEIPTS_SETUP.sql already contains this column.
ALTER TABLE public.events
 ADD COLUMN IF NOT EXISTS payment_link text NOT NULL DEFAULT '';
ALTER TABLE public.events DROP CONSTRAINT IF EXISTS events_payment_link_format_check;
ALTER TABLE public.events ADD CONSTRAINT events_payment_link_format_check
 CHECK (payment_link = '' OR
  (payment_method = 'receipt' AND length(payment_link) <= 500 AND
   payment_link ~ '^https://send[.]monobank[.]ua/jar/[A-Za-z0-9_-]{4,100}/?([?][^#[:space:]]*)?$'));
