-- Styloteka / Telegram forum topics for event announcements.
-- Safe to run again: existing groups, subscribers, events and posts remain unchanged.
-- NULL means that event posts continue to the group's main topic as before.

ALTER TABLE public.telegram_groups
    ADD COLUMN IF NOT EXISTS event_thread_id bigint;

COMMENT ON COLUMN public.telegram_groups.event_thread_id IS
    'Telegram message_thread_id of the forum topic for event announcements and reminders. NULL = main group.';
