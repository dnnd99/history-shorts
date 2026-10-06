# History Shorts — cerita tokoh sejarah, otomatis

Alur satu video (<60 detik, vertikal 1080x1920):
topik → fakta Wikipedia → script Gemini → suara Edge TTS → gambar domain publik Wikimedia
→ render FFmpeg (zoom/pan + subtitle per kata) → upload YouTube (private).

Semua gratis. Tidak pakai Puppeteer dan Whisper (subtitle diambil dari timing kata Edge TTS),
jadi ringan untuk VPS 5 GB.

## 1. Install di VPS
```bash
sudo apt install -y ffmpeg python3-venv fonts-dejavu-core
cd history-shorts
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # isi GEMINI_API_KEY
```
Tambah swap 2-4 GB kalau belum ada.

## 2. Setup upload YouTube (sekali saja)
1. Buat channel YouTube manual.
2. Google Cloud Console: buat project, aktifkan **YouTube Data API v3**.
3. OAuth consent screen: set status **In production** (kalau tetap "Testing", token mati tiap 7 hari).
4. Buat OAuth Client ID tipe **Desktop app**, unduh sebagai `client_secret.json`.
5. Di **laptop** (ada browser): taruh `client_secret.json` di folder ini, jalankan `python auth.py`,
   login ke akun channel. Salin `token.json` yang muncul ke folder proyek di VPS.

Catatan: project API yang belum diaudit Google memaksa video jadi private. Untuk publik otomatis,
ajukan audit API YouTube. Sebelum itu, publikasikan manual dari YouTube Studio.

## 3. Jalankan
```bash
python pipeline.py --no-upload        # tes: render saja, hasil di out/<tokoh>/video.mp4
python pipeline.py                    # render + upload private
python pipeline.py --topic "Hannibal" # paksa topik tertentu
```
Topik diambil berurutan dari `topics.txt` dan yang sudah dipakai dicatat di `state.json`.

**Kalau stok habis**, skrip otomatis meminta Gemini mengusulkan ~30 tokoh baru dan menambahkannya ke
`topics.txt`. Tidak ada yang kembar: tiap usulan dicek ke Wikipedia (redirect disamakan, jadi
"Napoleon" = "Napoleon Bonaparte"), halaman yang tidak ada atau disambiguasi dibuang, dan semua yang
sudah ada di daftar maupun `state.json` dilewati. Nonaktifkan dengan `AUTO_REFILL=false` di `.env`.
Isi stok manual kapan saja: `python pipeline.py --refill`.

## 4. Jadwal (cron, lebih ringan dari n8n)
```
0 9 * * * cd /path/history-shorts && .venv/bin/python pipeline.py >> run.log 2>&1
```
n8n opsional. Kalau mau, panggil perintah yang sama lewat node Execute Command
(di n8n versi baru node ini mati secara default).

## Aturan main
- Cek tiap video di YouTube Studio sebelum publik: LLM tetap bisa salah fakta.
- Gambar hanya lisensi public domain/CC0. `ALLOW_CC_BY=true` menambah CC BY (kredit otomatis ke deskripsi).
- Letakkan musik bebas royalti di folder `music/` (mp3). Opsional, volumenya 10%.
- Variasikan topik dan sudut cerita supaya tidak dianggap konten massal berulang.
