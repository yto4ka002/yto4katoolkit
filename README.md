# yto4katoolkit (Anime Toolkit)

Console utility for batch video processing, audio and subtitle stream manipulation, remuxing, and uploading media to Telegram channels via TDLib.

---

## Language
- [Русский](#инструкция-на-русском)
- [English](#english-guide)

---

## Инструкция на русском

### Описание функций
1. **Telegram Uploader** — пакетная загрузка видеофайлов в Telegram-канал через TDLib. Создание превью, добавление moov atom (faststart) для мгновенного воспроизведения, отображение прогресса и скорости загрузки.
2. **Remux в MP4** — быстрая переупаковка видеофайлов из форматов MKV, AVI, TS в MP4 без перекодирования видеопотока (`-c copy`). Поддержка выбора аудиодорожек.
3. **Smart Rename** — пакетное переименование серий по шаблону `[Серия]_[Сезон]_[ID]`. Автоматический поиск метаданных по базе Shikimori.
4. **Нарезка (Split)** — разделение видеофайлов по заданному размеру (например, 1900 МБ) через `mkvmerge` без потери качества.
5. **Склейка (Merge)** — объединение нескольких видеофайлов в один с помощью `mkvmerge`.
6. **BDRemux Compress** — сжатие тяжелых видеофайлов с использованием аппаратного энкодера HEVC (H.265) при постоянном качестве CQ=16.
7. **Озвучки и субтитры** — вшивание внешних дорожек (Softsub), впекание субтитров (Hardsub), извлечение и переименование потоков.
8. **Настройки** — конфигурация Telegram API ID, API Hash, целевого канала и уведомлений через Telegram-бота.

### Установка и запуск

1. **Клонирование репозитория:**
   ```bash
   git clone https://github.com/yto4ka002/yto4katoolkit.git
   cd yto4katoolkit
   ```

2. **Установка зависимостей:**
   ```bash
   pip install -r requirements.txt
   ```
   *Требуется Python 3.10+ и библиотека `tdjson==1.8.64.post1`.*

3. **Внешние утилиты:**
   В папку `tools/` необходимо поместить:
   - `ffmpeg.exe`
   - `ffprobe.exe`
   - `mkvmerge.exe`

4. **Конфигурация:**
   Скопируйте `config.example.json` в `config.json` и заполните свои данные Telegram API.

5. **Запуск скрипта:**
   ```bash
   python yto4kanime.py
   ```

6. **Сборка бинарного файла:**
   ```bash
   pyinstaller AnimeToolkit.spec --noconfirm
   ```

---

## English Guide

### Features Overview
1. **Telegram Uploader** — batch upload videos to Telegram channels using TDLib. Features thumbnail generation, faststart streaming optimization, and real-time upload progress.
2. **Remux to MP4** — fast container conversion from MKV, AVI, TS to MP4 without video re-encoding (`-c copy`). Allows internal and external audio selection.
3. **Smart Rename** — batch rename episodes into the standard format `[Episode]_[Season]_[ID]` with automatic metadata lookup.
4. **Split** — split large video files by file size limits (e.g. 1900 MB) using `mkvmerge` losslessly.
5. **Merge** — concatenate multiple video files into a single file with `mkvmerge`.
6. **BDRemux Compress** — hardware-accelerated HEVC (H.265) compression for high-bitrate releases with CQ=16 quality profile.
7. **Audio & Subtitle Manager** — manage audio and subtitle streams: softsub muxing, hardsub burn-in, stream extraction, and track metadata editing.
8. **Settings** — configure Telegram API credentials, target channels, and bot notification tokens.

### Installation & Usage

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yto4ka002/yto4katoolkit.git
   cd yto4katoolkit
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   *Requires Python 3.10+ and `tdjson==1.8.64.post1`.*

3. **External Tools:**
   Place the following executables inside the `tools/` directory:
   - `ffmpeg.exe`
   - `ffprobe.exe`
   - `mkvmerge.exe`

4. **Configuration:**
   Copy `config.example.json` to `config.json` and specify your Telegram API credentials.

5. **Run:**
   ```bash
   python yto4kanime.py
   ```

6. **Build executable:**
   ```bash
   pyinstaller AnimeToolkit.spec --noconfirm
   ```
