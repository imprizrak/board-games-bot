Styloteka 5.3.40 — правильна маршрутизація оплати (банка monobank + квитанція)

ПРИЧИНА, ПІДТВЕРДЖЕНА ЖУРНАЛОМ:
2026-11-21 17:22:55 UTC / 19:22:55 Київ, Android Telegram для події №21
звернувся POST /api/events/21/payment/start і отримав HTTP 503.
У Supabase для цієї події payment_method='receipt', price_kop=10000,
і payment_link заповнений. Вебсервер перевіряв відсутній токен
MONOBANK_ACQUIRING_TOKEN до перевірки типу платежу події.

ВИПРАВЛЕНО:
- Сервер спочатку перевіряє саму подію, її статус, ціну та спосіб оплати.
- Події з payment_method='receipt' НЕ потребують токена еквайрингу monobank;
  /payment/start повертає код receipt_payment_required (409).
- Старий WebView або посилання pay_21, що помилково звертається до
  /payment/start, перевіряє GET /api/events/<id>/receipt і, якщо
  подія приймає квитанції, відкриває належну форму з кнопкою банки.
- Кнопка «Перевірити оплату» відображається тільки для явних
  автоматичних платежів monobank (payment_method='monobank').
- Реальна автоматична інтеграція monobank не змінена; вона досі потребує
  конфігурації еквайрингу. Ручна перевірка квитанцій залишається обов'язковою.

ВСТАНОВЛЕННЯ:
1. У GitHub imprizrak/board-games-bot замінити ОБИДВА файли app.py та
   index.html на версії з архіву.
2. Дочекатися успішного автоматичного деплою Render (main).
3. Закрити Telegram Mini App повністю та відкрити подію №21 знову.
4. Натиснути «Я йду — квитанція», відкрити банку, сплатити 100 грн,
   прикріпити квитанцію, підтвердити її як адміністратор.

НЕ ПОТРІБНО:
- Ніяких SQL-міграцій та змін Supabase; налаштування події №21 уже правильне.
- Токен еквайрингу monobank не потрібен для платежів на банку з квитанцією.

ТЕСТИ (НЕ ПЕРЕВОДЯТЬ РЕАЛЬНИХ ГРОШЕЙ):
python -m unittest -v test_payment_routing_server_v5340.py
node test_payment_routing_ui_v5340.js
python -m unittest -v test_receipt_upload_v5339.py test_event_submissions_v5337.py test_event_links_v5338.py test_event_submissions_ui_v5337.py test_receipt_upload_ui_v5339.py test_ios_widgets_regression.py

Увага: на момент створення архіву код НЕ опубліковано на Render.
Перевірка на реальному Android Telegram потрібна після встановлення.
