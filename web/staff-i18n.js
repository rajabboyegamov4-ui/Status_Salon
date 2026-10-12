// Russian for the staff pages (admin, master's panel, orders desk). Loaded before the page's own script.
//
// The pages are written in Uzbek. With Russian chosen, every piece of text the page shows is looked up here
// as it appears (exact phrases first, then patterns for text with numbers and dates in it). Names, notes and
// other typed data are wrapped in Unicode isolates by the pages and are never touched.
// The language is the staff member's own (staff.language on the server, kept in sync); changing it reloads the page.
(() => {
  const KEY = "staff-lang", PENDING = "staff-lang-pending";
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* storage off */ } },
  };
  const q = new URLSearchParams(location.search).get("lang");
  if (q === "uz" || q === "ru") {  // from the bot's button: remembered, then dropped from the address so a later switch sticks
    store.set(KEY, q);
    try { const u = new URL(location.href); u.searchParams.delete("lang"); history.replaceState(history.state, "", u); } catch { /* sandboxed */ }
  }
  const LANG = store.get(KEY) === "ru" ? "ru" : "uz";
  const SLUG = location.pathname.split("/").filter(Boolean)[0] || "";
  window.STAFF_LANG = LANG;
  document.documentElement.lang = LANG;

  // ---------- the server: errors in this language; the language follows the account ----------
  const origFetch = window.fetch.bind(window);
  let syncing = false;
  async function adopt(me) {
    if (!me || !me.language || syncing) return;
    if (store.get(PENDING)) {  // chosen on the login screen: the account takes it
      store.set(PENDING, null);
      if (me.language !== LANG) { syncing = true; await putLang(LANG).catch(() => {}); syncing = false; }
      return;
    }
    if (me.language !== LANG) { store.set(KEY, me.language); location.reload(); }
  }
  const putLang = (l) => origFetch(`/api/s/${SLUG}/staff/me/language`, {
    method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Lang": l }, body: JSON.stringify({ language: l }),
  }).then((r) => { if (!r.ok) throw r.status; return r; });
  window.fetch = async (input, init = {}) => {
    const url = typeof input === "string" ? input : input?.url || "";
    if (url.startsWith("/api/")) { const h = new Headers(init.headers || {}); h.set("X-Lang", LANG); init = { ...init, headers: h }; }
    const res = await origFetch(input, init);
    if (res.ok && /\/staff\/(me|login|webapp)$/.test(url.split("?")[0])) res.clone().json().then(adopt).catch(() => {});
    return res;
  };
  // A button with data-lang-toggle switches; signed in, the account (and its bot) switches too.
  document.addEventListener("click", async (e) => {
    const b = e.target.closest?.("[data-lang-toggle]"); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const next = LANG === "ru" ? "uz" : "ru";
    store.set(KEY, next);
    try { await putLang(next); store.set(PENDING, null); } catch { store.set(PENDING, "1"); }
    location.reload();
  }, true);
  window.langButtonText = () => (LANG === "ru" ? "UZ" : "RU");

  if (LANG !== "ru") return;

  // ---------- exact phrases ----------
  const D = {
    // common
    "Salon Boshqaruvi": "Управление", "Usta paneli": "Панель мастера", "BOSHQARUV PANELI": "ПАНЕЛЬ УПРАВЛЕНИЯ", "Boshqaruv": "Управление",
    "Chiqish": "Выйти", "Kirish": "Войти", "Login": "Логин", "Parol": "Пароль", "Panelga kirish": "Вход в панель",
    "Salon rahbari yoki administrator login va paroli bilan kiring.": "Войдите с логином и паролем руководителя или администратора.",
    "Salon rahbari bergan login va parol bilan kiring.": "Войдите с логином и паролем, которые дал руководитель.",
    "Rahbar yoki administrator logini bilan kiring.": "Войдите с логином руководителя или администратора.",
    "Demo:": "Демо:", "Demo: rahbar": "Демо: руководитель", ", administrator": ", администратор", ", parol": ", пароль", ". Admin:": ". Админ:",
    "Internet aloqasi yo'q. Qayta urinib ko'ring.": "Нет подключения к интернету. Попробуйте снова.", "Internet aloqasi yo'q": "Нет подключения к интернету",
    "Serverda xatolik, birozdan keyin urinib ko'ring": "Ошибка сервера, попробуйте чуть позже", "Sessiya tugadi, qaytadan kiring": "Сессия закончилась, войдите снова",
    "Xatolik yuz berdi": "Произошла ошибка", "Yuklanmoqda…": "Загрузка…", "Saqlanmoqda…": "Сохраняем…", "Saqlash": "Сохранить", "Saqlandi": "Сохранено",
    "Yopish": "Закрыть", "Orqaga": "Назад", "Qo'shish": "Добавить", "O'chirish": "Удалить", "O'chirildi": "Удалено", "Nusxa": "Копировать",
    "Nusxa olindi": "Скопировано", "Raqam nusxalandi": "Номер скопирован", "Topilmadi": "Не найдено", "Yo'q": "Нет", "Ha, bekor qilish": "Да, отменить",
    "Bekor qilish": "Отменить", "Bekor": "Отменена", "Ixtiyoriy": "Необязательно", "Izoh (ixtiyoriy)": "Комментарий (необязательно)", "yoki": "или",
    "Obuna muddati tugagan. Panel faqat ko'rish rejimida ishlaydi.": "Срок подписки истёк. Панель работает только для просмотра.",
    "Obuna tugagan: panel faqat ko'rish rejimida.": "Подписка истекла: панель только для просмотра.",
    "Obuna tugagan: panel faqat ko'rish rejimida": "Подписка истекла: панель только для просмотра",
    "Salon platforma tomonidan to'xtatilgan": "Работа приостановлена платформой", "Salon obunasi tugagan": "Подписка истекла",
    "Salon vaqtincha to'xtatilgan": "Работа временно приостановлена", "Salon topilmadi. Manzilni tekshiring.": "Не найдено. Проверьте адрес.",
    "Salonda filial yo'q. Platforma bilan bog'laning.": "Нет филиалов. Свяжитесь с платформой.", "Filial topilmadi": "Филиал не найден",
    "Bu vaqt hozirgina band bo'ldi. Boshqa vaqtni tanlang.": "Это время только что заняли. Выберите другое.",
    "Bu vaqt hozirgina band bo'ldi. Iltimos, boshqa vaqtni tanlang.": "Это время только что заняли. Пожалуйста, выберите другое.",
    "Rasmni o'qib bo'lmadi": "Не удалось прочитать изображение",
    // roles, statuses
    "Salon rahbari": "Руководитель", "Administrator": "Администратор", "Usta": "Мастер", "Admin": "Админ", "Mijoz": "Клиент", "mijoz": "клиент",
    "Kutilmoqda": "Ожидается", "kutilmoqda": "ожидается", "Keldi": "Пришёл", "Kelmadi": "Не пришёл", "Bekor qilingan": "Отменена", "Telefonsiz": "Без телефона",
    "Telefonsiz mijoz": "Клиент без телефона", "✓ Keldi": "✓ Пришёл", "Keldi / kelmadi": "Пришли / не пришли",
    // sections
    "Kunlik jadval": "Расписание на день", "Jadval": "Расписание", "Xodimlar": "Сотрудники", "Ish vaqti": "Рабочее время", "Xizmatlar": "Услуги",
    "Salon va ustalar": "Профиль и мастера", "Profilim": "Мой профиль", "Baholar": "Оценки", "Mijozlar fikri": "Отзывы клиентов",
    "Ustalar jadvali": "Расписание мастеров", "Xizmatlar va narxlar": "Услуги и цены", "Mening jadvalim": "Моё расписание",
    "Mijoz saytida ko'rinadi": "Видно на сайте для клиентов", "+ Usta qo'shish": "+ Добавить мастера", "+ Xizmat qo'shish": "+ Добавить услугу",
    "+ Yangi bron": "+ Новая запись", "+ Mijoz": "+ Клиент", "+ Rasm": "+ Фото", "+ Ish rasmi": "+ Фото работы", "Bronlar": "Записи", "Bugun": "Сегодня",
    "Ertaga": "Завтра", "Kecha": "Вчера", "Oldingi kun": "Предыдущий день", "Keyingi kun": "Следующий день", "Sana": "Дата", "Filial": "Филиал",
    // day board
    "Ishlandi, so'm": "Заработано, сум", "Kutilmoqda, so'm": "Ожидается, сум", "Bandlik": "Загрузка", "Dam olish": "Выходной",
    "Bo'sh joyni bosing: o'sha master va vaqtga yangi bron ochiladi. Chiziqli joylar: usta ishlamaydigan vaqt.":
      "Нажмите на свободное место: откроется новая запись к этому мастеру на это время. Заштрихованное: мастер не работает.",
    "Bu kunga bron yo'q.": "На этот день записей нет.", "Vaqt": "Время", "Xizmat": "Услуга", "Narx, so'm": "Цена, сум", "Holat": "Статус",
    "Narx": "Цена", "Bron tafsiloti": "Детали записи", "Bronni ko'chirish": "Перенос записи", "Ko'chirish": "Перенести",
    "Yangi vaqt (faqat bo'sh)": "Новое время (только свободное)", "Bo'sh vaqt yo'q": "Свободного времени нет",
    "Xizmatlar va narx o'zgarmaydi. Mijozga Telegram orqali xabar boradi (agar ulangan bo'lsa).": "Услуги и цена не меняются. Клиент получит сообщение в Telegram (если подключён).",
    "Bronni bekor qilasizmi?": "Отменить запись?", "Vaqt bo'shaydi. Mijozga Telegram orqali xabar boradi (agar ulangan bo'lsa).": "Время освободится. Клиент получит сообщение в Telegram (если подключён).",
    "Yangi bron": "Новая запись", "Mijoz ismi": "Имя клиента", "Telefon (ixtiyoriy)": "Телефон (необязательно)", "Masalan, Aziz": "Например, Азиз",
    "Xorijiy raqamni + bilan yozing (masalan +7 916 123 45 67). O'zi kelib, raqam bermasa bo'sh qoldiring: \"Telefonsiz\" bo'lib yoziladi.":
      "Иностранный номер пишите с + (например +7 916 123 45 67). Если клиент пришёл сам и не дал номер, оставьте пустым: запишется «Без телефона».",
    "Vaqt (faqat bo'sh)": "Время (только свободное)", "Bron bekor qilindi": "Запись отменена", "Bron qo'shildi": "Запись добавлена",
    "Sanani tanlang": "Выберите дату", "Qo'ng'iroq": "Позвонить", "Telefon": "Телефон",
    "Fade, tepasi 4 sm": "Фейд, сверху 4 см", "Soqolni qisqa, kontur aniq": "Бороду коротко, чёткий контур", "Birinchi marta keladi": "Первый визит",
    // team
    "Ism": "Имя", "Lavozim": "Должность", "Oxirgi kirish": "Последний вход", "hali kirmagan": "ещё не входил", "· siz": "· вы", "· o'chirilgan": "· отключён",
    "Yangi parol": "Новый пароль", "Parolni saqlash": "Сохранить пароль", "Yana bir marta": "Ещё раз",
    "Har bir usta o'z login-paroli bilan faqat o'z bronlarini ko'radi. O'chirilgan usta saytda ko'rinmaydi va panelga kira olmaydi.":
      "Каждый мастер со своим логином и паролем видит только свои записи. Отключённый мастер не виден на сайте и не может войти в панель.",
    "Mening parolim": "Мой пароль", "Hozirgi parol": "Текущий пароль", "Yangi parol (kamida 6 belgi)": "Новый пароль (не меньше 6 символов)",
    "Boshqa qurilmalardagi kirishlar yopiladi.": "Входы на других устройствах будут закрыты.", "Parolni o'zgartirish": "Сменить пароль",
    "Boshqa telefonlarda qaytadan kirish kerak bo'ladi": "На других телефонах нужно будет войти заново",
    "Yangi xodim": "Новый сотрудник", "Yangi": "Новый", "Ismi": "Имя", "Masalan, Diyor": "Например, Диёр", "Login (lotin harf, raqam)": "Логин (латиница, цифры)",
    "Parol (kamida 6 belgi)": "Пароль (не меньше 6 символов)", "Administrator barcha bronlarni ko'radi va usta qo'sha oladi.": "Администратор видит все записи и может добавлять мастеров.",
    "Darajasi": "Уровень", "Ismini yozing": "Напишите имя", "Login 3-40 ta lotin harf yoki raqam bo'lsin (bo'sh joysiz)": "Логин: 3–40 латинских букв или цифр (без пробелов)",
    "Parol kamida 6 belgi bo'lsin": "Пароль не меньше 6 символов", "Xodim qo'shildi": "Сотрудник добавлен", "Parol yangilandi": "Пароль обновлён",
    "Hozirgi parolni yozing": "Введите текущий пароль", "Parol o'zgartirildi": "Пароль изменён", "Yangi parol kamida 6 belgi bo'lsin": "Новый пароль не меньше 6 символов",
    "Yangi parollar bir xil emas": "Новые пароли не совпадают", "Login yoki parol noto'g'ri": "Неверный логин или пароль", "Eski parol noto'g'ri": "Старый пароль неверный",
    "Bu login band. Boshqasini tanlang.": "Этот логин занят. Выберите другой.", "Manzil": "Адрес",
    // working hours
    "Haftalik ish vaqti": "Рабочее время на неделю", "Mijoz saytida faqat shu vaqtdagi bo'sh joylar chiqadi": "На сайте показываются только свободные места в это время",
    "Ishlaydi": "Работает", "Dam": "Выходной", "Hammasiga qo'llash": "Применить ко всем", "Shu vaqtni ishlaydigan barcha kunlarga qo'yish": "Поставить это время на все рабочие дни",
    "Tushlik kabi bir martalik tanaffusni pastda qo'shing.": "Разовый перерыв, например обед, добавьте ниже.", "Dam olish va tanaffus": "Выходные и перерывы",
    "Bir martalik: kasallik, tushlik, bayram yoki qo'shimcha ish kuni": "Разово: болезнь, обед, праздник или дополнительный рабочий день",
    "Turi": "Тип", "Masalan: tushlik, shifokorga": "Например: обед, к врачу", "Keyingi 30 kun": "Ближайшие 30 дней", "Dam olish kuni": "Выходной день",
    "Tanaffus": "Перерыв", "Qo'shimcha ish": "Дополнительная работа", "Kun bo'yi": "Весь день", "Hozircha yo'q": "Пока нет", "Hozircha yo'q.": "Пока нет.",
    "Ustalar yo'q. \"Xodimlar\" bo'limidan qo'shing.": "Мастеров нет. Добавьте в разделе «Сотрудники».",
    "Vaqt ishlaydigan barcha kunlarga qo'yildi": "Время поставлено на все рабочие дни", "Boshlanish va tugash vaqtini to'g'ri kiriting": "Введите корректное время начала и окончания",
    "Ish tugash vaqti boshlanishidan keyin bo'lishi kerak": "Время окончания должно быть позже начала", "Bir kunning ish vaqtlari ustma-ust tushmasin": "Интервалы одного дня не должны пересекаться",
    "O'tgan kunni o'zgartirib bo'lmaydi": "Прошедший день изменить нельзя", "Oilaviy marosim": "Семейное мероприятие", "Tushlik": "Обед",
    // services
    "Hali xizmat yo'q. \"+ Xizmat qo'shish\" ni bosing.": "Услуг пока нет. Нажмите «+ Добавить услугу».", "barcha ustalar": "все мастера",
    "hech kim bajarmaydi": "никто не выполняет", "Narx o'zgarsa, yangi bronlarga qo'llanadi; oldin yozilgan bronlar eski narxida qoladi.":
      "Новая цена действует для новых записей; уже сделанные записи остаются по старой цене.",
    "Xizmatni tahrirlash": "Редактирование услуги", "Yangi xizmat": "Новая услуга", "Nomi": "Название", "Masalan, Soch olish": "Например, Стрижка",
    "Narxi, so'm": "Цена, сум", "Davomiyligi, daqiqa": "Длительность, мин", "Bo'lim (ixtiyoriy)": "Раздел (необязательно)", "Bo'lim": "Раздел",
    "Faol: saytda ko'rinadi": "Активна: видна на сайте", "O'chirilgan: saytda ko'rinmaydi": "Отключена: не видна на сайте", "O'chirilgan": "Отключено",
    "Kim bajaradi va o'z narxi": "Кто выполняет и своя цена", "Narx bo'sh bo'lsa, asosiy narx olinadi. Top-master uchun qimmatroq narx yozing.":
      "Если цена пустая, берётся основная. Для топ-мастера укажите цену выше.", "Xizmat nomini yozing": "Напишите название услуги", "Narx noto'g'ri": "Неверная цена",
    "Davomiylik 5 dan 600 daqiqagacha bo'lsin": "Длительность от 5 до 600 минут", "Narxni yozing": "Укажите цену", "Xizmat saqlandi": "Услуга сохранена",
    "Xizmat qo'shildi": "Услуга добавлена", "Xizmat topilmadi": "Услуга не найдена",
    // profile and bot
    "Mijoz saytining bosh qismi": "Шапка сайта для клиентов", "Muqova": "Обложка", "Muqovani almashtirish": "Сменить обложку", "Muqova rasmi yuklash": "Загрузить обложку",
    "Salon": "Бизнес", "Salon haqida": "О бизнесе", "Instagram": "Instagram", "Telegram bot": "Telegram-бот", "Ulangan": "Подключён", "Botni ochish": "Открыть бота",
    "Yangi bronlar haqida xabar": "Сообщения о новых записях", "Telegram'da xabar olish": "Получать сообщения в Telegram", "Ulanmagan: mijozlar hali kira olmaydi": "Не подключён: клиенты пока не могут войти",
    "Telegram'da": "В Telegram", "ni oching va": "откройте и", "deb yozing.": "напишите.", "Bot nomini yozing (masalan,": "Напишите имя бота (например,",
    "), keyin oxiri": "), затем username, который заканчивается на", "bilan tugaydigan username bering.": ".", "BotFather bergan tokenni pastga qo'yib, \"Ulash\"ni bosing.":
      "Вставьте ниже токен от BotFather и нажмите «Подключить».", "Bot tokeni": "Токен бота", "Ulash": "Подключить",
    "Token shifrlab saqlanadi va boshqa hech kimga ko'rinmaydi.": "Токен хранится в зашифрованном виде и никому не виден.", "Bot uzildi": "Бот отключён",
    "Telegram xabarlari o'chirildi": "Сообщения в Telegram отключены", "Xabarlar yoqildi ✅": "Сообщения включены ✅", "Hali ulanmagan: Telegram'da Start ni bosing": "Ещё не подключено: нажмите Start в Telegram",
    "Yoqilgan": "Включено", "O'chiq": "Выключено", "Tekshirish": "Проверить", "Tugmani bosing, Telegram ochiladi:": "Нажмите кнопку, откроется Telegram:", "Start": "Start",
    "Sizga yozilgan yangi va bekor qilingan bronlar Telegram'ga keladi. Botdagi \"Boshqaruv\" tugmasi shu panelni ochadi.":
      "Новые и отменённые записи к вам приходят в Telegram. Кнопка «Управление» в боте открывает эту панель.",
    "ni bosing. Keyin har bir yangi bron haqida xabar keladi va panelni botdan ochasiz.": ". После этого о каждой новой записи придёт сообщение, а панель можно открыть из бота.",
    "Yandex'da tekshirish": "Проверить в Яндексе", "Google'da tekshirish": "Проверить в Google", "Joylashuv hali kiritilmagan": "Локация ещё не указана",
    "Rasmlar va joylashuv": "Фото и локация", "Masalan: metro yonida, Korzinka ro'parasida": "Например: у метро, напротив Korzinka", "Filial telefoni": "Телефон филиала",
    "Xaritadagi joy": "Место на карте", "Google yoki Yandex xaritadan havolani shu yerga qo'ying": "Вставьте сюда ссылку из Google или Яндекс Карт",
    "Xaritada salonni topib, \"Ulashish\" havolasini qo'ying, koordinata o'zi chiqadi.": "Найдите себя на карте и вставьте ссылку «Поделиться», координаты определятся сами.",
    "Kenglik": "Широта", "Uzunlik": "Долгота", "Mo'ljal": "Ориентир", "Hali baho yo'q": "Оценок пока нет", "Tajriba, yil": "Опыт, лет",
    "Yo'nalishlari (vergul bilan)": "Направления (через запятую)", "O'zi haqida": "О себе", "Qayerda o'qigan, qanday uslubda ishlaydi": "Где учился, в каком стиле работает",
    "Hali baho yo'q. Mijoz tashrifdan keyin \"Bronlarim\" bo'limida ustaga yulduzcha qo'yadi.": "Оценок пока нет. После визита клиент ставит звёзды мастеру в разделе «Мои записи».",
    "Yashirilgan: saytda ko'rinmaydi va reytingga qo'shilmaydi": "Скрыт: не виден на сайте и не учитывается в рейтинге", "Yashirish": "Скрыть", "Qayta ko'rsatish": "Показать снова",
    "Salon ma'lumoti saqlandi": "Данные сохранены", "Kenglik va uzunlik raqam bo'lsin, masalan 41.2756 va 69.2034": "Широта и долгота должны быть числами, например 41.2756 и 69.2034",
    "Filial saqlandi": "Филиал сохранён", "Rasm o'chirildi": "Фото удалено", "Fikr yashirildi": "Отзыв скрыт", "Fikr qayta ko'rsatildi": "Отзыв снова показан",
    "Rasm yuklanmoqda…": "Загружаем фото…", "Rasm qo'shildi": "Фото добавлено", "Koordinata topildi": "Координаты найдены",
    "Bu havolada koordinata yo'q. Xaritadagi to'liq havolani yoki 41.31, 69.24 ko'rinishida yozing.": "В этой ссылке нет координат. Вставьте полную ссылку с карты или напишите в виде 41.31, 69.24.",
    "Mijoz faqat \"Keldi\" deb belgilangan tashrifga, bir marta baho qo'ya oladi. Yulduzlarni o'zgartirib bo'lmaydi, haqoratli yoki reklama otzivni yashirish mumkin.":
      "Клиент может оценить только визит, отмеченный «Пришёл», и только один раз. Звёзды изменить нельзя; оскорбительный или рекламный отзыв можно скрыть.",
    "Rasmni o'chirish": "Удалить фото", "Rasmni almashtirish": "Сменить фото", "Rasm yuklash": "Загрузить фото", "Yuz rasmi yuklash": "Загрузить фото лица",
    "Bu ma'lumot mijoz saytida ko'rinadi: yuzingiz, tajribangiz va ishlaringiz rasmlari.": "Это видно на сайте для клиентов: ваше фото, опыт и фото работ.",
    "Masalan: ayollar uchun go'zallik saloni, tajribali ustalar va qulay joy": "Например: женский салон красоты, опытные мастера и удобное место",
    "o'rtacha": "в среднем", "besh yulduz": "пять звёзд", "baho": "оценка", "Muqovani yuklash": "Загрузить обложку",
    // master's panel
    "Keyingi": "Следующий", "Telefon orqali": "По телефону", "Hozir": "Сейчас", "Keyingi mijoz": "Следующий клиент", "dam olish": "выходной",
    "Bugun dam olish kuni": "Сегодня выходной", "O'zingiz belgilagansiz.": "Вы отметили сами.", "Ish kuni qilish": "Сделать рабочим",
    "Jadval bo'yicha bu kun ishlamaysiz.": "По расписанию в этот день вы не работаете.", "Tanaffusni olib tashlash": "Убрать перерыв",
    "Vaqt bo'shaydi, mijozga Telegramda xabar boradi.": "Время освободится, клиент получит сообщение в Telegram.",
    "Mijoz qo'shish": "Добавить клиента", "O'zingizning kreslongizga": "В ваше кресло", "Masalan: fade, tepasi 4 sm": "Например: фейд, сверху 4 см",
    "Vaqtni tanlang": "Выберите время", "Ko'chirilmoqda…": "Переносим…", "Yangi vaqtni tanlang": "Выберите новое время", "Olib tashlash": "Убрать",
    "Vaqtni band qilish": "Занять время", "Bu vaqtga mijoz yozila olmaydi": "На это время клиент не сможет записаться", "30 daqiqa tanaffus": "Перерыв 30 минут",
    "1 soat tanaffus": "Перерыв 1 час", "Tanaffus hozirdan boshlanadi.": "Перерыв начинается сейчас.",
    "Bu kunga yangi bron olinmaydi. Bor bronlar o'zida qoladi, mijozlarga qo'ng'iroq qiling.": "На этот день новые записи не принимаются. Существующие остаются, позвоните клиентам.",
    "Ha, ishlamayman": "Да, не работаю", "Masalan: tushlik": "Например: обед", "Band qilish": "Занять", "Bugun ishlamayman": "Сегодня не работаю",
    "Shu kuni ishlamayman": "В этот день не работаю", "Bugungi ish vaqti tugagan": "Рабочее время на сегодня закончилось",
    "Telefon raqamini to'liq yozing yoki bo'sh qoldiring": "Введите номер полностью или оставьте пустым", "Boshlanish va tugash vaqtini tanlang": "Выберите время начала и окончания",
    "Tugash vaqti boshlanishidan keyin bo'lsin": "Время окончания должно быть позже начала", "Olib tashlandi": "Убрано", "Keldi deb belgilandi": "Отмечено: пришёл",
    "Kelmadi deb belgilandi": "Отмечено: не пришёл", "Avval xizmatni tanlang.": "Сначала выберите услугу.", "Bo'sh vaqtlar yuklanmoqda…": "Загружаем свободное время…",
    "Boshlanishi": "Начало", "Tugashi": "Окончание", "Boshqa vaqt": "Другое время", "Bron yo'q": "Записей нет", "Bu kunda band qilingan vaqt yo'q.": "В этот день занятого времени нет.",
    "Bu kunda bo'sh vaqt yo'q. Boshqa kunni tanlang.": "В этот день свободного времени нет. Выберите другой день.", "Bu kunga hali hech kim yozilmagan.": "На этот день пока никто не записался.",
    "Xizmatni tanlang": "Выберите услугу", "masalan, jasur": "например, jasur", "Yana": "Ещё", "so'm ishlandi": "сум заработано", "so'm": "сум", "bot": "бот",
    "Bu vaqtga yangi bron olinmaydi.": "На это время новые записи не принимаются.", "mijozlaringiz": "ваши клиенты", "admin ko'rinishi": "вид администратора",
    "Yig'ish": "Свернуть", "Ko'proq": "Ещё", "Batafsil": "Подробнее",
    // orders desk
    "Buyurtmalar": "Заказы", "Ochiq": "Открытые", "Yopilgan": "Закрытые", "Mahsulotlar": "Товары", "Sozlamalar": "Настройки",
    "Qabul qilindi": "Принят", "Tayyor": "Готов", "Yo'lda": "В пути", "Berildi": "Выдан", "Bekor qilindi": "Отменён",
    "✅ Qabul qilish": "✅ Принять", "🍳 Tayyor": "🍳 Готов", "🚚 Yo'lga chiqdi": "🚚 Выехал", "✅ Berildi": "✅ Выдан",
    "Bekor qilinsinmi? Mijozga xabar boradi.": "Отменить? Клиент получит сообщение.", "🏪 Olib ketadi": "🏪 Самовывоз",
    "Ochiq buyurtma yo'q. Yangi buyurtma kelganda shu yerda va Telegram'da ko'rinadi.": "Открытых заказов нет. Новый заказ появится здесь и в Telegram.",
    "Yopilgan buyurtma yo'q.": "Закрытых заказов нет.", "+ Mahsulot": "+ Товар", "Tugadi": "Нет в наличии", "Bor": "Есть", "Tahrirlash": "Изменить",
    "Katalog bo'sh. Birinchi mahsulotni qo'shing.": "Каталог пуст. Добавьте первый товар.", "Olib ketish mumkin": "Можно забрать самому",
    "Yetkazib beramiz": "Доставляем", "Yetkazish narxi, so'm": "Стоимость доставки, сум", "0 — bepul": "0 — бесплатно", "Eng kam buyurtma, so'm": "Минимальный заказ, сум",
    "0 — cheklovsiz": "0 — без ограничений", "Mahsulot": "Товар", "Mahsulotni tahrirlash": "Редактирование товара", "Yangi mahsulot": "Новый товар",
    "Masalan, Tortlar": "Например, Торты", "Tavsif": "Описание", "Rasm": "Фото", "Rasmni saqlagandan keyin qo'shasiz.": "Фото можно добавить после сохранения.",
    "Buyurtmalarni rahbar yoki administrator ko'radi.": "Заказы видят руководитель или администратор.", "Nomini yozing": "Напишите название",
    "Qo'shildi. Endi rasm yuklashingiz mumkin.": "Добавлено. Теперь можно загрузить фото.", "Rasm yuklanmadi": "Фото не загрузилось", "Rasm yuklandi": "Фото загружено",
    "Mijoz katalogni botdagi \"🛍 Buyurtma berish\" tugmasidan ochadi:": "Клиент открывает каталог кнопкой «🛍 Сделать заказ» в боте:",
    "· Buyurtmalar": "· Заказы", "· tugadi": "· нет в наличии", "Boshqa": "Другое", "· panel": "· панель", "· hozir": "· сейчас",
    "Yangi bronlar Telegram'ga keladi": "Новые записи приходят в Telegram",
  };
  const MONTH = { yanvar: "января", fevral: "февраля", mart: "марта", aprel: "апреля", may: "мая", iyun: "июня", iyul: "июля", avgust: "августа",
    sentabr: "сентября", oktabr: "октября", noyabr: "ноября", dekabr: "декабря" };
  const MON3 = { yan: "янв", fev: "фев", mar: "мар", apr: "апр", may: "мая", iyu: "июн", avg: "авг", sen: "сен", okt: "окт", noy: "ноя", dek: "дек" };
  const WDAY = { dushanba: "понедельник", seshanba: "вторник", chorshanba: "среда", payshanba: "четверг", juma: "пятница", shanba: "суббота", yakshanba: "воскресенье" };
  const WD2 = { Du: "Пн", Se: "Вт", Ch: "Ср", Pa: "Чт", Ju: "Пт", Sh: "Сб", Ya: "Вс" };
  const cap = (s, like) => (like[0] === like[0].toUpperCase() ? s[0].toUpperCase() + s.slice(1) : s);
  const T = (s) => D[s.trim()] || s;  // a word inside a pattern: the status, the kind of break...
  Object.assign(D, WD2);

  // ---------- text with numbers, dates and names in it ----------
  const RULES = [
    [/(\d{1,2})-(yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sentabr|oktabr|noyabr|dekabr)\b/g, (_, d, m) => `${d} ${MONTH[m]}`],
    [/(\d{1,2})-(yan|fev|mar|apr|may|iyu|avg|sen|okt|noy|dek)\b/g, (_, d, m) => `${d} ${MON3[m]}`],
    [/\b(dushanba|seshanba|chorshanba|payshanba|juma|shanba|yakshanba)\b/gi, (w) => cap(WDAY[w.toLowerCase()], w)],
    [/^(Du|Se|Ch|Pa|Ju|Sh|Ya)(?= \d)/, (w) => WD2[w]],
    [/(\d+) soat (\d+) daqiqadan keyin/g, "через $1 ч $2 мин"], [/(\d+) daqiqadan keyin/g, "через $1 мин"], [/(\d+) soatdan keyin/g, "через $1 ч"],
    [/(\d+) (?:daq|daqiqa)\b/g, "$1 мин"], [/\bso'm\b/g, "сум"], [/(\d+) bron\b/g, "$1 зап."], [/(\d+) baho\b/g, "оценок: $1"],
    [/\bbarcha ustalar\b/g, "все мастера"], [/\bhech kim bajarmaydi\b/g, "никто не выполняет"],
    [/^Bot nomini yozing \(masalan, (.+)\), keyin oxiri$/, "Напишите имя бота (например, $1), затем username, который заканчивается на"],
    [/^Bo'sh /, "Свободно "], [/^Tanaffus\b/, "Перерыв"], [/^Band: /, "Занято: "], [/^Ishlari \((.+)\)$/, "Работы ($1)"],
    [/^Salom, /, "Привет, "], [/· mijozlaringiz$/, "· ваши клиенты"], [/· admin ko'rinishi$/, "· вид администратора"],
    [/^bugun (\d)/, "сегодня $1"], [/^kecha (\d)/, "вчера $1"], [/^Filial: /, "Филиал: "], [/\(yetkazish /, "(доставка "],
    [/^(Usta|Bron|Xizmat) #/, (_, w) => ({ Usta: "Мастер #", Bron: "Запись #", Xizmat: "Услуга #" })[w]],
    [/^(.+) profili saqlandi$/, "Профиль сохранён: $1"], [/^(\d+) ta rasm yuklanmoqda…$/, "Загружаем фото: $1…"],
    [/^(.+) deb o'zgartirildi$/, (_, s) => `Статус изменён: ${T(s)}`], [/^(.+) deb belgilandi$/, (_, s) => `Отмечено: ${T(s).toLowerCase()}`],
    [/^O'zgartirish: (.+)$/, (_, s) => `Изменить: ${T(s[0].toUpperCase() + s.slice(1)).toLowerCase()}`],
    [/^Xato: (.+)$/, (_, s) => `Ошибка: ${T(s[0].toUpperCase() + s.slice(1)).toLowerCase()}`],
    [/^(.+) qayta yoqildi$/, "Снова включён: $1"], [/^(.+) o'chirildi$/, "Отключён: $1"],
    [/^(.+): tugash vaqti boshlanishidan keyin bo'lsin$/, "$1: время окончания должно быть позже начала"], [/^(.+): ish vaqti saqlandi$/, "$1: рабочее время сохранено"],
    [/(\d+) ta bron shu vaqtga to'g'ri keladi, mijozlarga qo'ng'iroq qiling/, "На это время приходится записей: $1, позвоните клиентам"],
    [/^(.+) qo'shildi$/, (_, s) => `Добавлено: ${T(s).toLowerCase()}`], [/^Yangi bron: /, "Новая запись: "], [/,? va yana (\d+) ta$/, " и ещё $1"], [/, yana (\d+) ta$/, ", ещё $1"],
    [/^Yangi buyurtma #(\d+)$/, "Новый заказ #$1"], [/^Mijoz qo'shildi: /, "Клиент добавлен: "],
    [/^Ko'chirildi: (.+)\. Mijozga xabar boradi$/, "Перенесено: $1. Клиент получит сообщение"], [/^(.+): dam olish$/, "$1: выходной"],
    [/^(.+) ga ko'chirish$/, "Перенести на $1"], [/^Qo'shish · /, "Добавить · "], [/^(.+) band qilingan$/, "Занято: $1"], [/^(.+) ishlamaysizmi\?$/, "$1 не работаете?"],
    [/^(.+) ishlaydi$/, "$1: работает"], [/^(.+): boshlanishi$/, "$1: начало"], [/^(.+): tugashi$/, "$1: конец"], [/^(.+) narxi$/, "Цена: $1"],
    [/\bBugun\b/g, "Сегодня"], [/\bErtaga\b/g, "Завтра"], [/^(\d+) ta$/, "$1"],
  ];
  const ISO = /(⁨[^⁩]*⁩|⁦[^⁩]*⁩)/;
  function part(s) {
    const core = s.trim(); if (!core || !/[A-Za-z]/.test(core)) return s;
    if (D[core]) return s.replace(core, D[core]);
    let out = core;
    for (const [rx, to] of RULES) out = out.replace(rx, to);
    return out === core ? s : s.replace(core, out);
  }
  function tr(s) {
    if (!s || !/[A-Za-z]/.test(s)) return s;
    const core = s.trim();
    if (D[core]) return s.replace(core, D[core]);
    return s.split(ISO).map((p, i) => (i % 2 ? p : part(p))).join("");
  }
  window.trStaff = tr;
  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE"]);
  function fixText(n) { if (!SKIP.has(n.parentNode?.nodeName)) { const v = n.nodeValue, t = tr(v); if (t !== v) n.nodeValue = t; } }
  function fixAttrs(el) {
    for (const a of ["placeholder", "title", "aria-label"]) { const v = el.getAttribute(a); if (v) { const t = tr(v); if (t !== v) el.setAttribute(a, t); } }
  }
  function walk(root) {
    if (root.nodeType === 3) return fixText(root);
    if (root.nodeType !== 1 || SKIP.has(root.nodeName)) return;
    fixAttrs(root);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeType === 3 ? fixText(n) : fixAttrs(n);
  }
  new MutationObserver((list) => {
    for (const m of list) {
      if (m.type === "characterData") fixText(m.target);
      else if (m.type === "attributes") fixAttrs(m.target);
      else m.addedNodes.forEach(walk);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["placeholder", "title", "aria-label"] });
  document.addEventListener("DOMContentLoaded", () => { walk(document.body); document.title = tr(document.title); });
  for (const f of ["alert", "confirm", "prompt"]) { const o = window[f]; window[f] = (msg, ...rest) => o.call(window, tr(String(msg ?? "")), ...rest); }
})();
