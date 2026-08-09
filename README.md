# Telegram Bot — Modullashtirilgan versiya

Bu kod asl `main.py` (3289 qator) dan **hech qanday mantiq o'zgartirilmasdan**,
faqat 5 ta faylga bo'lib chiqarildi. Har bir funksiya, har bir qator — original bilan bir xil.

## Fayllar tuzilishi

| Fayl | Nima bor |
|---|---|
| `config.py` | Sozlamalar, holatlar (states), tugma matnlari, logging |
| `database.py` | SQLite bilan ishlash — jadval yaratish, CRUD funksiyalari |
| `helpers.py` | Klaviaturalar, obuna tekshirish, anime/fasl ko'rsatish funksiyalari |
| `handlers.py` | Barcha Telegram handler'lar (foydalanuvchi + admin) |
| `main.py` | Botni yig'ib, ishga tushiruvchi asosiy fayl |

## Deploy qilish — MUHIM, o'zgarmaydi

1. **Start command hosting'da o'zgarmaydi**: hali ham `python main.py`.
2. Barcha 5 ta `.py` fayl **bitta papkada** bo'lishi kerak (masalan repo root'ida,
   `main.py` bilan bir joyda) — chunki fayllar bir-birini `from config import *`
   kabi nisbiy (relative) import orqali chaqiradi.
3. `requirements.txt` **o'zgarmagan** — bir xil qoldi.
4. `BOT_TOKEN` environment variable hostingda avvalgidek sozlanishi kerak.

## anime.db (baza) xavfsizligi

**Baza tuzilmasi, jadval nomlari, ustunlar — hech biri o'zgarmadi.**
Shuning uchun:
- Eski `anime.db` faylini yangi kod bilan bir xil papkaga qo'ysangiz, bot
  uni **hech qanday muammosiz** o'qiy oladi — ma'lumot yo'qolmaydi.
- `init_db()` funksiyasi ham o'zgarmagan holda ko'chirildi (database.py ichida) —
  demak eski bazadagi migratsiya tekshiruvlari (ALTER TABLE va h.k.) avvalgidek ishlaydi.

## Tavsiya etilgan deploy tartibi (xatoning oldini olish uchun)

1. Hosting'da **test/staging muhit** bo'lsa, avval o'sha yerda sinab ko'ring.
2. Agar faqat bitta production muhit bo'lsa:
   - `anime.db` faylining **zaxira nusxasini** oling (yoki botning o'zidagi
     "💾 Backup Olish" tugmasidan foydalaning).
   - Yangi 5 ta faylni yuklang, `anime.db` ni **o'sha joyida** qoldiring
     (fayl nomi va joylashuvi o'zgarmasligi kerak).
   - Botni qayta ishga tushiring va `/start` buyrug'i bilan tekshiring.
   - Agar biror xatolik chiqsa, eski bitta-fayl `main.py` versiyasiga qaytarish
     — bu ham xavfsiz, chunki baza tuzilmasi o'zgarmagan.

## Tekshiruvlar (men tomonimdan bajarilgan)

- ✅ Barcha 158 ta funksiya asl fayldan yangi fayllarga **to'liq** ko'chirilgan
  (birortasi yo'qolmagan, birortasi takrorlanmagan).
- ✅ Barcha 5 fayl sintaksis xatosiz (`py_compile` orqali tekshirildi).
- ✅ Statik tahlil (`symtable` orqali) — hech bir faylda "aniqlanmagan nom"
  (undefined name / import xatosi) topilmadi.
