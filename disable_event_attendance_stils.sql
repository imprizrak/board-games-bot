-- Styloteka 5.3.44 — stop new attendance currency awards (preserve all history).
-- Attendance QR check-in remains functional; existing balances and payouts are not modified.
BEGIN;
ALTER TABLE public.events ALTER COLUMN attendance_reward_stils SET DEFAULT 0;
ALTER TABLE public.event_submissions ALTER COLUMN attendance_reward_stils SET DEFAULT 0;
UPDATE public.events SET attendance_reward_stils = 0
 WHERE status='scheduled' AND attendance_reward_stils <> 0;
UPDATE public.event_submissions SET attendance_reward_stils = 0
 WHERE status='pending' AND attendance_reward_stils <> 0;

CREATE OR REPLACE FUNCTION public.styloteka_confirm_event_checkin(
  p_event_id bigint, p_ticket_id uuid, p_admin_id bigint
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO ''
AS $function$
DECLARE
 v_ticket public.event_checkins%ROWTYPE;
 v_event public.events%ROWTYPE;
 v_person public.event_rsvps%ROWTYPE;
BEGIN
 IF p_event_id<=0 OR p_admin_id<=0 OR p_ticket_id IS NULL THEN
   RETURN jsonb_build_object('status','invalid_ticket');
 END IF;
 SELECT * INTO v_event FROM public.events WHERE id=p_event_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','event_not_found'); END IF;
 IF v_event.status<>'scheduled' THEN RETURN jsonb_build_object('status','event_closed'); END IF;
 SELECT * INTO v_ticket FROM public.event_checkins
 WHERE event_id=p_event_id AND ticket_id=p_ticket_id FOR UPDATE;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','invalid_ticket'); END IF;
 SELECT * INTO v_person FROM public.event_rsvps
 WHERE event_id=p_event_id AND telegram_user_id=v_ticket.telegram_user_id AND status='going'
 ORDER BY id ASC LIMIT 1;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','not_registered'); END IF;
 IF v_ticket.checked_in_at IS NOT NULL THEN
   RETURN jsonb_build_object('status','already_checked_in','display_name',v_person.display_name,
     'checked_in_at',v_ticket.checked_in_at,'reward_stils',0);
 END IF;
 UPDATE public.event_checkins
 SET checked_in_at=now(),checked_in_by=p_admin_id
 WHERE event_id=p_event_id AND ticket_id=p_ticket_id;
 RETURN jsonb_build_object('status','checked_in','display_name',v_person.display_name,
   'telegram_user_id',v_ticket.telegram_user_id,'reward_stils',0,'new_balance',NULL);
END;
$function$;
REVOKE ALL ON FUNCTION public.styloteka_confirm_event_checkin(bigint,uuid,bigint)
 FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.styloteka_confirm_event_checkin(bigint,uuid,bigint)
 TO service_role;
COMMIT;
