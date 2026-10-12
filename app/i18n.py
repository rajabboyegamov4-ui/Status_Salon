"""Two languages: Uzbek (the default) and Russian.

Clients choose theirs in the bot or on the site (users.language); staff choose theirs in the bot or the panel
(staff.language). Pages send `X-Lang: ru`, and error messages from the API come back in Russian.
What a business typed itself (its name, services, addresses) is shown as typed.
"""

import re

LANGS = ("uz", "ru")
DEFAULT = "uz"


def norm(code: str | None) -> str:
    """A Telegram or browser language code to one of ours: Russian for ru/uk/be/kk, Uzbek for anything else."""
    code = (code or "").lower()[:2]
    return "ru" if code in ("ru", "uk", "be", "kk") else "uz" if code not in LANGS else code


def pick(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT


# ---------- what the specialist is called ----------

# Uzbek term -> Russian: one, many, to whom (dative), whom (genitive/accusative)
TERM_RU = {
    "Usta": ("Мастер", "Мастера", "мастеру", "мастера"),
    "Kosmetolog": ("Косметолог", "Косметологи", "косметологу", "косметолога"),
    "Shifokor": ("Врач", "Врачи", "врачу", "врача"),
    "Massajchi": ("Массажист", "Массажисты", "массажисту", "массажиста"),
    "Veterinar": ("Ветеринар", "Ветеринары", "ветеринару", "ветеринара"),
    "Xodim": ("Сотрудник", "Сотрудники", "сотруднику", "сотрудника"),
    "Mutaxassis": ("Специалист", "Специалисты", "специалисту", "специалиста"),
}


def term_ru(term: str) -> list[str]:
    return list(TERM_RU.get(term, TERM_RU["Mutaxassis"]))


def term(term_uz: str, lang: str) -> str:
    return term_ru(term_uz)[0] if lang == "ru" else term_uz


KIND_LABEL_RU = {
    "barber": "Мужской барбершоп", "beauty": "Женский салон красоты", "cosmetology": "Косметология",
    "dental": "Стоматология", "clinic": "Многопрофильная клиника", "spa": "SPA и массаж", "auto": "Автосервис",
    "vet": "Ветеринария", "shop": "Заказы (цветы, торты, магазин)", "other": "Другой бизнес",
}

WEEKDAYS = {
    "uz": ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"),
    "ru": ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"),
}

ORDER_LABEL = {
    "uz": {"new": "Yangi", "accepted": "Qabul qilindi", "ready": "Tayyor", "delivering": "Yo'lda", "done": "Berildi",
           "cancelled": "Bekor qilindi"},
    "ru": {"new": "Новый", "accepted": "Принят", "ready": "Готов", "delivering": "В пути", "done": "Выдан",
           "cancelled": "Отменён"},
}


def som(n: int, lang: str) -> str:
    return f"{n:,}".replace(",", " ") + (" сум" if lang == "ru" else " so'm")


# ---------- messages in the bot ----------

M: dict[str, tuple[str, str]] = {
    # language
    "lang.pick": ("Tilni tanlang:", "Выберите язык:"),
    "lang.set": ("✅ Til: O'zbekcha", "✅ Язык: Русский"),
    # client: buttons
    "btn.book": ("📅 Bron qilish", "📅 Записаться"),
    "btn.order": ("🛍 Buyurtma berish", "🛍 Сделать заказ"),
    "btn.contact": ("📱 Raqamni yuborish", "📱 Отправить номер"),
    "btn.login_yes": ("✅ Ha, kirish", "✅ Да, войти"),
    "btn.cancel": ("❌ Bekor qilish", "❌ Отменить"),
    "btn.come": ("✅ Kelaman", "✅ Приду"),
    "btn.yes_cancel": ("Ha, bekor qilish", "Да, отменить"),
    "btn.no": ("Yo'q", "Нет"),
    "menu.book": ("Bron qilish", "Записаться"),
    "menu.order": ("Buyurtma", "Заказ"),
    "act.both": ("Bron yoki buyurtma", "Записаться или заказать"),
    "act.order": ("Buyurtma berish", "Сделать заказ"),
    "act.book": ("Bron qilish", "Записаться"),
    # client: the conversation
    "hello.new": ("Assalomu alaykum! Bu <b>{name}</b> boti.\nObuna bo'lish uchun telefon raqamingizni yuboring 👇",
                  "Здравствуйте! Это бот <b>{name}</b>.\nЧтобы подписаться, отправьте свой номер телефона 👇"),
    "hello.known": ("Assalomu alaykum{hello}! <b>{name}</b> ga xush kelibsiz.\n{act} uchun pastdagi tugmani bosing 👇",
                    "Здравствуйте{hello}! Добро пожаловать в <b>{name}</b>.\n{act}: нажмите кнопку ниже 👇"),
    "press.button": ("{act} uchun pastdagi tugmani bosing 👇", "{act}: нажмите кнопку ниже 👇"),
    "press.button.msgs": ("{act} uchun pastdagi tugmani bosing. Xabarlar shu yerga keladi.",
                          "{act}: нажмите кнопку ниже. Сообщения будут приходить сюда."),
    "login.share": ("<b>{name}</b> saytiga kirish uchun telefon raqamingizni yuboring 👇",
                    "Чтобы войти на сайт <b>{name}</b>, отправьте свой номер телефона 👇"),
    "login.confirm": ("<b>{name}</b> saytiga kirish so'raldi.\nAgar hozir saytda o'zingiz bosgan bo'lsangiz, tugmani bosing. "
                      "Aks holda bu xabarga e'tibor bermang.",
                      "Запрошен вход на сайт <b>{name}</b>.\nЕсли это вы нажали на сайте, нажмите кнопку. "
                      "Если нет, просто проигнорируйте это сообщение."),
    "login.expired": ("Kirish muddati tugagan. Saytda qaytadan bosing.", "Время входа истекло. Нажмите на сайте ещё раз."),
    "login.ok": ("✅ Kirdingiz. Saytga qayting.", "✅ Вы вошли. Вернитесь на сайт."),
    "login.ok.long": ("✅ Kirdingiz. Saytga qayting, bronni davom ettiring.", "✅ Вы вошли. Вернитесь на сайт и продолжите запись."),
    "contact.own": ("Faqat o'zingizning raqamingizni yuboring: pastdagi tugmani bosing.",
                    "Отправьте только свой номер: нажмите кнопку ниже."),
    "contact.bad": ("Telefon raqamingizni qabul qila olmadik. Iltimos, salonga qo'ng'iroq qiling.",
                    "Не удалось принять ваш номер. Пожалуйста, позвоните нам."),
    "contact.login_ok": ("✅ Rahmat! Saytga qayting, bron shu yerdan davom etadi.\nTasdiq va eslatmalar shu chatga keladi.",
                         "✅ Спасибо! Вернитесь на сайт, запись продолжится там.\nПодтверждения и напоминания будут приходить в этот чат."),
    "contact.subscribed": ("✅ Obuna bo'ldingiz! Tasdiq va eslatmalar shu chatga keladi.",
                           "✅ Вы подписались! Подтверждения и напоминания будут приходить в этот чат."),
    "cq.done": ("Bajarildi", "Готово"),
    "cq.no_booking": ("Bu bron topilmadi", "Запись не найдена"),
    "cq.cancelled": ("Bron bekor qilindi", "Запись отменена"),
    "cq.come": ("Rahmat, sizni kutamiz!", "Спасибо, ждём вас!"),
    "cq.rated": ("Rahmat! Bahoyingiz qabul qilindi.", "Спасибо! Оценка принята."),
    "rated": ("{stars} Rahmat! Fikr yozmoqchi bo'lsangiz, saytdagi \"Bronlarim\" bo'limida yozishingiz mumkin.",
              "{stars} Спасибо! Отзыв можно написать на сайте в разделе «Мои записи»."),
    "client.cancelled_by_me": ("❌ {when} dagi bron bekor qilindi.", "❌ Запись на {when} отменена."),
    # client: booking messages
    "b.confirmed": ("✅ <b>Bron tasdiqlandi</b>\n{when} · {branch}\n{term}: {master}\nManzil: {address}\nJami: {total}",
                    "✅ <b>Запись подтверждена</b>\n{when} · {branch}\n{term}: {master}\nАдрес: {address}\nИтого: {total}"),
    "b.remind.24h": ("⏰ Ertaga ({when}) sizni kutamiz.\n{term}: {master} · {branch}",
                     "⏰ Ждём вас завтра ({when}).\n{term}: {master} · {branch}"),
    "b.remind.2h": ("⏰ 2 soatdan keyin ({when}) sizni kutamiz.\n{term}: {master} · {branch}",
                    "⏰ Ждём вас через 2 часа ({when}).\n{term}: {master} · {branch}"),
    "b.cancelled": ("❌ {when} dagi bron bekor qilindi. {branch}", "❌ Запись на {when} отменена. {branch}"),
    "b.moved": ("🔁 <b>Bron vaqti o'zgardi</b>\nYangi vaqt: {when} · {branch}\n{term}: {master}",
                "🔁 <b>Время записи изменено</b>\nНовое время: {when} · {branch}\n{term}: {master}"),
    "b.review": ("Tashrifingiz uchun rahmat! {master}ning ishini baholang:", "Спасибо за визит! Оцените работу: {master}"),
    # client: orders
    "o.new": ("🧾 <b>Buyurtma #{n} qabul qilindi</b>\nTez orada tasdiqlaymiz. Holatini shu chatda ko'rasiz.",
              "🧾 <b>Заказ #{n} получен</b>\nСкоро подтвердим. Статус будет приходить в этот чат."),
    "o.accepted": ("👍 <b>Buyurtma #{n} tasdiqlandi</b>\nTayyorlayapmiz.", "👍 <b>Заказ #{n} подтверждён</b>\nГотовим."),
    "o.ready": ("✅ <b>Buyurtma #{n} tayyor</b>\n{ready}", "✅ <b>Заказ #{n} готов</b>\n{ready}"),
    "o.ready.delivery": ("Kuryerga topshiramiz.", "Передаём курьеру."),
    "o.ready.pickup": ("Olib ketishingiz mumkin.", "Можно забирать."),
    "o.delivering": ("🚚 <b>Buyurtma #{n} yo'lda</b>\nKuryer tez orada yetib boradi.", "🚚 <b>Заказ #{n} в пути</b>\nКурьер скоро будет."),
    "o.done": ("🙏 <b>Buyurtma #{n} berildi</b>\nXaridingiz uchun rahmat!", "🙏 <b>Заказ #{n} выдан</b>\nСпасибо за покупку!"),
    "o.cancelled": ("❌ <b>Buyurtma #{n} bekor qilindi</b>", "❌ <b>Заказ #{n} отменён</b>"),
    "o.fee": ("Yetkazish", "Доставка"),
    "o.total": ("💰 Jami: <b>{total}</b>", "💰 Итого: <b>{total}</b>"),
    "o.to": ("🚚 Yetkazish: {address}", "🚚 Доставка: {address}"),
    "o.pickup": ("🏪 Olib ketish", "🏪 Самовывоз"),
    # staff
    "client": ("Mijoz", "Клиент"),
    "s.new": ("🆕 <b>Yangi bron</b>", "🆕 <b>Новая запись</b>"),
    "s.cancelled": ("❌ <b>Bron bekor qilindi</b>", "❌ <b>Запись отменена</b>"),
    "s.moved": ("🔁 <b>Bron vaqti o'zgardi</b>", "🔁 <b>Время записи изменено</b>"),
    "so.new": ("🆕 <b>Yangi buyurtma #{n}</b>", "🆕 <b>Новый заказ #{n}</b>"),
    "so.cancelled": ("❌ <b>Buyurtma #{n} bekor qilindi</b>", "❌ <b>Заказ #{n} отменён</b>"),
    "so.accepted": ("👍 <b>Buyurtma #{n} qabul qilindi</b>", "👍 <b>Заказ #{n} принят</b>"),
    "so.ready": ("✅ <b>Buyurtma #{n} tayyor</b>", "✅ <b>Заказ #{n} готов</b>"),
    "so.delivering": ("🚚 <b>Buyurtma #{n} yo'lda</b>", "🚚 <b>Заказ #{n} в пути</b>"),
    "so.done": ("🙏 <b>Buyurtma #{n} berildi</b>", "🙏 <b>Заказ #{n} выдан</b>"),
    "sk.today": ("📋 Bugun", "📋 Сегодня"),
    "sk.tomorrow": ("📋 Ertaga", "📋 Завтра"),
    "sk.orders": ("📦 Buyurtmalar", "📦 Заказы"),
    "sk.panel": ("⚙️ Boshqaruv", "⚙️ Управление"),
    "sk.lang": ("🌐 Русский", "🌐 O'zbekcha"),
    "sb.panel": ("📋 Boshqaruv", "📋 Управление"),
    "sb.accept": ("✅ Qabul qilish", "✅ Принять"),
    "sb.ready": ("🍳 Tayyor", "🍳 Готов"),
    "sb.on_way": ("🚚 Yo'lga chiqdi", "🚚 Выехал"),
    "sb.given": ("✅ Berildi", "✅ Выдан"),
    "s.hello": ("Assalomu alaykum, {who}! <b>{name}</b> boshqaruvi pastdagi tugmalarda.",
                "Здравствуйте, {who}! Управление <b>{name}</b> на кнопках ниже."),
    "s.linked": ("✅ {who}, endi <b>{name}</b>dagi yangi, bekor qilingan va ko'chirilgan bronlar haqida xabar shu chatga keladi.\n\n"
                 "📋 <b>Bugun</b> va <b>Ertaga</b> tugmalari kunlik ro'yxatni ko'rsatadi, ⚙️ <b>Boshqaruv</b> panelni shu yerning o'zida ochadi.\n"
                 "🌐 Tilni pastdagi tugma bilan o'zgartirasiz.",
                 "✅ {who}, теперь сообщения о новых, отменённых и перенесённых записях <b>{name}</b> будут приходить в этот чат.\n\n"
                 "📋 Кнопки <b>Сегодня</b> и <b>Завтра</b> показывают список на день, ⚙️ <b>Управление</b> открывает панель прямо здесь.\n"
                 "🌐 Язык меняется кнопкой ниже."),
    "s.link_old": ("Havola eskirgan. Admin paneldagi \"Telegram'da xabar olish\" tugmasini qaytadan bosing.",
                   "Ссылка устарела. Нажмите «Получать сообщения в Telegram» в панели ещё раз."),
    "s.lang_set": ("✅ Til: O'zbekcha. Xabarlar va tugmalar endi o'zbekcha.", "✅ Язык: русский. Сообщения и кнопки теперь на русском."),
    "s.no_branch": ("Filial yo'q.", "Филиалов нет."),
    "s.today": ("Bugun", "Сегодня"),
    "s.tomorrow": ("Ertaga", "Завтра"),
    "s.no_bookings": ("Bron yo'q.", "Записей нет."),
    "s.count": ("{n} ta", "{n} шт."),
    "s.no_orders": ("📦 Ochiq buyurtma yo'q.", "📦 Открытых заказов нет."),
    "s.open_orders": ("📦 <b>Ochiq buyurtmalar</b> · {n} ta", "📦 <b>Открытые заказы</b> · {n}"),
    "s.orders_managers": ("Buyurtmalarni rahbar va administrator ko'radi.", "Заказы видят руководитель и администратор."),
    "s.order_missing": ("Bu buyurtma topilmadi", "Заказ не найден"),
    "s.cancel_ask": ("Bekor qilinsinmi? Mijozga xabar boradi.", "Отменить? Клиент получит сообщение."),
    "s.order_kept": ("Buyurtma o'z holicha qoldi", "Заказ оставлен без изменений"),
    "s.booking_kept": ("Bron o'z holicha qoldi", "Запись оставлена без изменений"),
    "s.expired": ("Obuna muddati tugagan", "Срок подписки истёк"),
    "s.not_yours": ("Bu sizning broningiz emas", "Это не ваша запись"),
    "s.not_active": ("Bu bron allaqachon faol emas", "Эта запись уже не активна"),
    "s.cancelled_told": ("❌ Bron bekor qilindi. Mijozga xabar yuborildi.", "❌ Запись отменена. Клиенту отправлено сообщение."),
    "s.cancelled_ok": ("Bekor qilindi", "Отменено"),
}


def t(lang: str | None, key: str, **kw) -> str:
    uz, ru = M[key]
    text = ru if lang == "ru" else uz
    return text.format(**kw) if kw else text


def words(key: str) -> set[str]:
    """Both languages' versions of a button, lowercased: a typed button press matches either."""
    return {x.lower() for x in M[key]}


# ---------- API error messages ----------

ERRORS_RU = {
    "Avval botga telefon raqamingizni yuboring": "Сначала отправьте боту свой номер телефона",
    "Avval tizimga kiring": "Сначала войдите",
    "Avval kiring": "Сначала войдите",
    "Baho faqat tashrifdan keyin qoldiriladi": "Оценку можно поставить только после визита",
    "Bekor qilingan bronni o'zgartirib bo'lmaydi": "Отменённую запись нельзя изменить",
    "Bir kunning ish vaqtlari ustma-ust tushmasin": "Рабочие интервалы одного дня не должны пересекаться",
    "Boshlanish va tugash vaqtini to'g'ri kiriting": "Введите корректное время начала и окончания",
    "Botni faqat salon egasi ulaydi": "Бота подключает только владелец",
    "Bron topilmadi": "Запись не найдена",
    "Bu Telegram panelga ulanmagan. Avval login va parol bilan kiring.":
        "Этот Telegram не привязан к панели. Сначала войдите по логину и паролю.",
    "Bu amal faqat salon rahbari yoki administrator uchun": "Это действие доступно только руководителю или администратору",
    "Bu biznes buyurtma qabul qilmaydi": "Этот бизнес не принимает заказы",
    "Bu bron allaqachon faol emas": "Эта запись уже не активна",
    "Bu buyurtma olib ketiladi": "Этот заказ на самовывоз",
    "Bu login band": "Этот логин занят",
    "Bu login band. Boshqasini tanlang.": "Этот логин занят. Выберите другой.",
    "Bu master tanlangan xizmatlarni bajarmaydi": "Этот специалист не выполняет выбранные услуги",
    "Bu rasmni rahbar yoki administrator o'chiradi": "Это фото удаляет руководитель или администратор",
    "Bu sizning broningiz emas": "Это не ваша запись",
    "Bu tashrifga baho allaqachon qo'yilgan": "Этот визит уже оценён",
    "Bu xodimni faqat salon rahbari boshqaradi": "Этим сотрудником управляет только руководитель",
    "Bunday xodim yo'q": "Такого сотрудника нет",
    "Buyurtma qabul qilingan. Bekor qilish uchun bizga qo'ng'iroq qiling.": "Заказ уже принят. Чтобы отменить, позвоните нам.",
    "Buyurtma topilmadi": "Заказ не найден",
    "Darajani salon rahbari belgilaydi": "Уровень назначает руководитель",
    "Eski parol noto'g'ri": "Старый пароль неверный",
    "Faqat JPG, PNG yoki WEBP rasm yuklang": "Загрузите изображение JPG, PNG или WEBP",
    "Faqat faol bronni ko'chirish mumkin": "Перенести можно только активную запись",
    "Faqat o'z profilingizni o'zgartira olasiz": "Можно изменять только свой профиль",
    "Faqat o'zingizga bron qo'sha olasiz": "Можно добавлять записи только к себе",
    "Faqat o'zingizning broningizni ko'chira olasiz": "Можно переносить только свои записи",
    "Faqat o'zingizning jadvalingizni o'zgartira olasiz": "Можно изменять только своё расписание",
    "Faqat yetkazib berish bor": "Только доставка",
    "Filial rasmini rahbar yoki administrator yuklaydi": "Фото филиала загружает руководитель или администратор",
    "Filial topilmadi": "Филиал не найден",
    "Ish tugash vaqti boshlanishidan keyin bo'lishi kerak": "Время окончания должно быть позже начала",
    "Joylashuv uchun kenglik va uzunlik birga kerak": "Для локации нужны и широта, и долгота",
    "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.": "Слишком много попыток. Попробуйте чуть позже.",
    "Kamida bitta xizmat tanlang": "Выберите хотя бы одну услугу",
    "Kirish muddati tugadi. Qaytadan bosing.": "Время входа истекло. Нажмите ещё раз.",
    "Kod eskirgan yoki topilmadi. Yangi kod so'rang.": "Код устарел или не найден. Запросите новый.",
    "Kod noto'g'ri": "Неверный код",
    "Kod yaqinda yuborildi. Bir daqiqadan keyin qayta so'rang.": "Код недавно отправлен. Запросите снова через минуту.",
    "Login yoki parol noto'g'ri": "Неверный логин или пароль",
    "Mahsulot topilmadi": "Товар не найден",
    "Mijoz baho qoldirgan, holatni o'zgartirib bo'lmaydi": "Клиент уже оставил оценку, статус изменить нельзя",
    "Mijoz bu salonga tegishli emas": "Клиент не относится к этому бизнесу",
    "O'tgan kunni o'zgartirib bo'lmaydi": "Прошедший день изменить нельзя",
    "O'zingizni o'chira olmaysiz": "Нельзя удалить самого себя",
    "Obuna muddati tugagan. Panel faqat ko'rish rejimida ishlaydi.": "Срок подписки истёк. Панель работает только для просмотра.",
    "Oraliq 2 oydan oshmasin": "Период не должен превышать 2 месяцев",
    "Otziv topilmadi": "Отзыв не найден",
    "Panelga kiring": "Войдите в панель",
    "Parol o'zgargan. Qaytadan kiring.": "Пароль изменён. Войдите снова.",
    "Rasm bo'sh": "Пустое изображение",
    "Rasm juda katta (3 MB dan oshmasin)": "Изображение слишком большое (не более 3 МБ)",
    "Rasm topilmadi": "Фото не найдено",
    "SMS o'chirilgan": "SMS отключены",
    "SMS orqali kirish o'chirilgan. Telegram orqali kiring.": "Вход по SMS отключён. Войдите через Telegram.",
    "SMS yuborib bo'lmadi. Birozdan keyin qayta urinib ko'ring.": "Не удалось отправить SMS. Попробуйте чуть позже.",
    "Salon boti hali ulanmagan. Iltimos, salonga qo'ng'iroq qiling.": "Бот ещё не подключён. Пожалуйста, позвоните нам.",
    "Salon rasmini rahbar yoki administrator yuklaydi": "Фото загружает руководитель или администратор",
    "Salon topilmadi": "Бизнес не найден",
    "Savat bo'sh": "Корзина пуста",
    "Savatdagi ba'zi mahsulotlar endi yo'q. Sahifani yangilang.": "Некоторых товаров из корзины больше нет. Обновите страницу.",
    "Tashrif vaqti hali kelmagan": "Время визита ещё не наступило",
    "Telefon raqam noto'g'ri. Namuna: +998 90 123 45 67": "Неверный номер. Пример: +998 90 123 45 67",
    "Telefon raqam noto'g'ri. Namuna: +998 90 123 45 67 yoki +7 916 123 45 67":
        "Неверный номер. Пример: +998 90 123 45 67 или +7 916 123 45 67",
    "Telegram ma'lumoti tasdiqlanmadi": "Данные Telegram не подтверждены",
    "Topilmadi": "Не найдено",
    "Urinishlar soni tugadi. Yangi kod so'rang.": "Попытки закончились. Запросите новый код.",
    "Usta bu filialda emas": "Специалист не работает в этом филиале",
    "Usta topilmadi": "Специалист не найден",
    "Xizmat topilmadi": "Услуга не найдена",
    "Xodim topilmadi": "Сотрудник не найден",
    "Yetkazib berish yo'q, olib ketish mumkin": "Доставки нет, только самовывоз",
    "Yetkazish manzilini yozing": "Укажите адрес доставки",
    "Yetkazish yoki olib ketishdan kamida bittasi yoqilgan bo'lsin": "Включите доставку или самовывоз (хотя бы одно)",
    "Noto'g'ri qiymat": "Неверное значение",
    "Bu vaqt hozirgina band bo'ldi. Iltimos, boshqa vaqtni tanlang.": "Это время только что заняли. Пожалуйста, выберите другое.",
    "Bu manzil band. Boshqa nom tanlang.": "Этот адрес занят. Выберите другое название.",
}

_PATTERNS_RU = [
    (re.compile(r"^Buyurtma holati: (.+)\. Bu amal endi mumkin emas\.$"),
     lambda m: f"Статус заказа: {_order_label_ru(m[1])}. Это действие уже невозможно."),
    (re.compile(r"^Eng kam buyurtma ([\d ]+) so'm$"), lambda m: f"Минимальный заказ {m[1]} сум"),
    (re.compile(r"^Ko'p xato urinish\. (\d+) daqiqadan keyin qayta urinib ko'ring\.$"),
     lambda m: f"Много неверных попыток. Попробуйте через {m[1]} мин."),
    (re.compile(r"^Ko'pi bilan (\d+) ta rasm\. Avval eskisini o'chiring\.$"),
     lambda m: f"Не более {m[1]} фото. Сначала удалите старое."),
    (re.compile(r"^Bronni boshlanishiga (\d+) soatdan kam qolganda bekor qilib bo'lmaydi\. Iltimos, salonga qo'ng'iroq qiling\.$"),
     lambda m: f"Запись нельзя отменить менее чем за {m[1]} ч до начала. Пожалуйста, позвоните нам."),
]


def _order_label_ru(uz: str) -> str:
    for key, label in ORDER_LABEL["uz"].items():
        if label == uz:
            return ORDER_LABEL["ru"][key]
    return uz


def error(detail, lang: str | None):
    """An API error message in the caller's language (Uzbek as written, Russian translated when known)."""
    if lang != "ru" or not isinstance(detail, str):
        return detail
    if detail in ERRORS_RU:
        return ERRORS_RU[detail]
    for rx, fn in _PATTERNS_RU:
        m = rx.match(detail)
        if m:
            return fn(m)
    return detail
