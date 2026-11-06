-- Styloteka 5.3.21 — окрема Telegram-гілка для нових настільних ігор.
-- Безпечне, повторно виконуване оновлення: жодні існуючі анонси чи події не видаляються.
ALTER TABLE public.telegram_groups
    ADD COLUMN IF NOT EXISTS game_thread_id bigint;

COMMENT ON COLUMN public.telegram_groups.game_thread_id IS
    'ID Telegram forum topic «Настілки» для повідомлень про нові ігри. NULL = не публікувати їх у групі до прив’язки /setgamestopic.';
