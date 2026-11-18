# ============================================================================
#  anime_toolkit.py — Anime Toolkit v8.0 (TDJSON + Audio/Voice Manager + EXE)
# ============================================================================

import os
import sys
import re
import json
import time
import glob
import shutil
import asyncio
import hashlib
import subprocess
import tempfile
import threading
import faulthandler
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

faulthandler.enable()

CYAN    = '\033[96m'
GREEN   = '\033[92m'
YELLOW  = '\033[93m'
RED     = '\033[91m'
MAGENTA = '\033[95m'
BOLD    = '\033[1m'
DIM     = '\033[2m'
RESET   = '\033[0m'

# ============================================================================
#  КОНФИГ
# ============================================================================
WORKERS_REMUX       = 2
TARGET_MB           = 1500
DEFAULT_MAXRATE     = 10000
CQ_VALUE            = 16
USE_CUDA_OUTPUT     = True

API_ID              = 10002648
API_HASH            = "bbf2f6a3646ca61a98e95ab834b1b585"
INVITE_LINK         = ""


def _get_script_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_APP_DIR        = _get_script_dir()
DEFAULT_TOOLKIT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "yto4katoolkit")
CONFIG_FILE         = os.path.join(BASE_APP_DIR, "config.json")
CACHE_FILE          = os.path.join(BASE_APP_DIR, "voice_cache.json")
PART_CACHE          = os.path.join(BASE_APP_DIR, "part_cache.json")
THUMB_CACHE_DIR     = ".thumbs"

VIDEO_EXTENSIONS    = ('.mp4', '.mkv', '.avi', '.mov', '.webm',
                       '.flv', '.ts', '.m4v')
AUDIO_EXTENSIONS    = ('.mka', '.aac', '.ac3', '.eac3', '.mp3',
                       '.flac', '.opus', '.ogg', '.wav', '.m4a', '.dts')
SUBTITLE_EXTENSIONS = ('.ass', '.ssa', '.srt', '.vtt', '.sub', '.sup')
FONT_EXTENSIONS     = ('.ttf', '.otf', '.ttc', '.woff', '.woff2')

TDLIB_SESSION_DIR   = os.path.join(os.path.expanduser("~"),
                                   ".anime_toolkit_tdjson_1864")

THUMB_TIMEOUT_SEC   = 180
FASTSTART_ON_UPLOAD = True
FASTSTART_MAX_MB    = 99999


def natural_sort_key(s):
    """Естественная сортировка файлов (1, 2, ..., 9, 10 вместо 1, 10, 2)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]


def send_telegram_notification(msg: str):
    """Отправка уведомления об окончании работы через Telegram Bot (если настроен)."""
    try:
        cfg = load_config()
        token = cfg.get("notify_bot_token")
        chat_id = cfg.get("notify_chat_id")
        if not token or not chat_id:
            return
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception:
        pass


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


# ============================================================================
#  УТИЛИТЫ
# ============================================================================
def _fmt_time(sec):
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def _make_bar(pct, width=24):
    pct = max(0.0, min(100.0, pct))
    filled = int(pct / 100 * width)
    return '█' * filled + '░' * (width - filled)


def print_banner():
    print(f'{CYAN}{BOLD}')
    print('  ___             _                        _____            _ _    _ _ ')
    print(' / _ \\           (_)                      |_   _|          | | |  (_) |')
    print('/ /_\\ \\_ __  _ __ _  ___ ___   ___  ___     | | ___   ___ | | | ___| |_ ')
    print('|  _  | \'_ \\| \'_ \\| |/ __/ _ \\ / _ \\/ __|    | |/ _ \\ / _ \\| | |/ / | __|')
    print('| | | | | | | | | | | (_| (_) |  __/\\__ \\    | | (_) | (_) | |   <| | |_ ')
    print('\\_| |_/_| |_|_| |_|_|\\___\\___/ \\___||___/    \\_/\\___/ \\___/|_|_|\\_\\_|\\__|')
    print(f'{RESET}')
    print(f'{YELLOW}{BOLD}      === Anime Toolkit v8.5 (TDJSON + Audio & Subtitles Manager) ==={RESET}')


@lru_cache(maxsize=None)
def _have(tool):
    if not tool:
        return False
    if os.path.isabs(tool):
        return os.path.exists(tool)
    if os.path.exists(tool):
        return True
    return shutil.which(tool) is not None


def get_tool_path(tool_name):
    bases = []
    if hasattr(sys, '_MEIPASS'):
        bases.append(sys._MEIPASS)
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        bases.append(exe_dir)
        bases.append(os.path.dirname(exe_dir))
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bases.append(script_dir)
    bases.append(os.path.dirname(script_dir))
    bases.append(os.getcwd())
    if DEFAULT_TOOLKIT_DIR not in bases:
        bases.append(DEFAULT_TOOLKIT_DIR)

    for base in bases:
        for sub in ('tools', 'bin', ''):
            p = os.path.join(base, sub, tool_name) if sub else os.path.join(base, tool_name)
            if os.path.isfile(p):
                return p
    return shutil.which(tool_name) or tool_name


def _pause():
    input(f'\n{DIM}Enter — продолжить...{RESET}')


# ============================================================================
#  FFPROBE / FFMPEG И АНАЛИЗ ОЗВУЧЕК
# ============================================================================
def _estimate_audio_kbps(line):
    l = line.lower()
    if 'dts-hd' in l or 'dtshd' in l:
        return 3000
    for k, v in (('aac', 192), ('eac3', 640), ('e-ac3', 640), ('ac3', 448),
                 ('dts', 1500), ('truehd', 3000), ('flac', 1000),
                 ('pcm', 1500), ('lpcm', 1500), ('opus', 128),
                 ('vorbis', 192), ('mp3', 192)):
        if k in l:
            return v
    return 256


def _probe_all_uncached(f, ffprobe, ffmpeg):
    if ffprobe and _have(ffprobe):
        try:
            r = subprocess.run(
                [ffprobe, '-v', 'error',
                 '-show_entries',
                 'format=duration:'
                 'stream=index,codec_type,codec_name,bit_rate,width,height,channels:'
                 'stream_tags=language,title,handler_name',
                 '-of', 'json', f],
                capture_output=True, text=True,
                encoding='utf-8', errors='ignore', timeout=30)
            data = json.loads(r.stdout or '{}')
            dur = float(data.get('format', {}).get('duration') or 0)
            video = None
            audio = []
            subtitles = []
            a_idx = 0
            s_idx = 0
            for s in data.get('streams', []):
                ctype = s.get('codec_type')
                if ctype == 'video' and video is None:
                    video = {
                        'width': int(s.get('width') or 0),
                        'height': int(s.get('height') or 0),
                        'codec': (s.get('codec_name') or '').lower(),
                    }
                elif ctype == 'audio':
                    codec = (s.get('codec_name') or 'unknown').lower()
                    br = s.get('bit_rate')
                    try:
                        br = int(br) // 1000 if br and br != 'N/A' else None
                    except (ValueError, TypeError):
                        br = None
                    tags = s.get('tags') or {}
                    lang = (tags.get('language') or tags.get('LANGUAGE') or 'und').strip()
                    title = (tags.get('title') or tags.get('TITLE') or '').strip()
                    channels = int(s.get('channels') or 2)
                    audio.append({
                        'a_idx': a_idx,
                        'stream_index': s.get('index', a_idx),
                        'codec': codec,
                        'bitrate': br,
                        'channels': channels,
                        'lang': lang,
                        'title': title,
                    })
                    a_idx += 1
                elif ctype == 'subtitle':
                    codec = (s.get('codec_name') or 'unknown').lower()
                    tags = s.get('tags') or {}
                    lang = (tags.get('language') or tags.get('LANGUAGE') or 'und').strip()
                    title = (tags.get('title') or tags.get('TITLE') or '').strip()
                    subtitles.append({
                        's_idx': s_idx,
                        'stream_index': s.get('index', s_idx),
                        'codec': codec,
                        'lang': lang,
                        'title': title,
                    })
                    s_idx += 1
            return {'duration': dur, 'video': video, 'audio': audio, 'subtitles': subtitles}
        except (subprocess.TimeoutExpired, json.JSONDecodeError,
                ValueError, OSError):
            pass

    r = subprocess.run([ffmpeg, '-hide_banner', '-i', f],
                       stderr=subprocess.PIPE, text=True,
                       encoding='utf-8', errors='ignore', timeout=30)
    err = r.stderr or ''
    m = re.search(r'Duration:\s*(\d+):(\d+):(\d+)\.(\d+)', err)
    dur = 0.0
    if m:
        h, mm, s, cs = m.groups()
        dur = int(h) * 3600 + int(mm) * 60 + int(s) + int(cs) / 100
    video = None
    audio = []
    subtitles = []
    a_idx = 0
    s_idx = 0
    lines = err.splitlines()
    for i, line in enumerate(lines):
        if 'Stream #' not in line:
            continue
        if 'Video:' in line and video is None:
            vm = re.search(r'(\d{3,5})x(\d{3,5})', line)
            cm = re.search(r'Video:\s*([a-zA-Z0-9_\-]+)', line)
            video = {
                'width': int(vm.group(1)) if vm else 0,
                'height': int(vm.group(2)) if vm else 0,
                'codec': cm.group(1).lower() if cm else '',
            }
        elif 'Audio:' in line:
            cm = re.search(r'Audio:\s*([a-zA-Z0-9_\-]+)', line)
            lm = re.search(r'Stream #\d+:\d+(?:\[0x[0-9a-fA-F]+\])?\(([a-zA-Z]{2,3})\):', line)
            codec = cm.group(1).lower() if cm else 'unknown'
            lang = lm.group(1).lower() if lm else 'und'
            title = ''
            for sub_line in lines[i + 1:i + 5]:
                if 'Stream #' in sub_line:
                    break
                tm = re.search(r'^\s*title\s*:\s*(.+)$', sub_line, re.IGNORECASE)
                if tm:
                    title = tm.group(1).strip()
                    break
            audio.append({
                'a_idx': a_idx,
                'stream_index': a_idx,
                'codec': codec,
                'bitrate': _estimate_audio_kbps(line),
                'channels': 2,
                'lang': lang,
                'title': title,
            })
            a_idx += 1
        elif 'Subtitle:' in line:
            cm = re.search(r'Subtitle:\s*([a-zA-Z0-9_\-]+)', line)
            lm = re.search(r'Stream #\d+:\d+(?:\[0x[0-9a-fA-F]+\])?\(([a-zA-Z]{2,3})\):', line)
            codec = cm.group(1).lower() if cm else 'unknown'
            lang = lm.group(1).lower() if lm else 'und'
            title = ''
            for sub_line in lines[i + 1:i + 5]:
                if 'Stream #' in sub_line:
                    break
                tm = re.search(r'^\s*title\s*:\s*(.+)$', sub_line, re.IGNORECASE)
                if tm:
                    title = tm.group(1).strip()
                    break
            subtitles.append({
                's_idx': s_idx,
                'stream_index': s_idx,
                'codec': codec,
                'lang': lang,
                'title': title,
            })
            s_idx += 1
    return {'duration': dur, 'video': video, 'audio': audio, 'subtitles': subtitles}


@lru_cache(maxsize=256)
def _probe_all_cached(f, mtime, ffprobe, ffmpeg):
    return _probe_all_uncached(f, ffprobe, ffmpeg)


def _probe_all(f, ffprobe, ffmpeg):
    try:
        mt = int(os.path.getmtime(f))
    except OSError:
        mt = 0
    try:
        return _probe_all_cached(f, mt, ffprobe, ffmpeg)
    except TypeError:
        return _probe_all_uncached(f, ffprobe, ffmpeg)


def _get_video_info(filepath, ffprobe, ffmpeg):
    p = _probe_all(filepath, ffprobe, ffmpeg)
    v = p.get('video')
    if not v or not v.get('width'):
        return None
    return {
        'width': v['width'],
        'height': v['height'],
        'duration': p['duration'],
        'codec': v['codec'],
    }


def _format_audio_desc(a):
    idx = a.get('a_idx', 0)
    lang = a.get('lang') or 'und'
    title = a.get('title') or ''
    codec = (a.get('codec') or 'unknown').upper()
    br = a.get('bitrate')
    ch = a.get('channels') or 2
    br_str = f', {br} kbps' if br else ''
    title_str = f' — "{title}"' if title else ''
    return f'Дорожка #{idx + 1} [{lang}]{title_str} ({codec}, {ch}ch{br_str})'


def _format_subtitle_desc(s):
    idx = s.get('s_idx', 0)
    lang = s.get('lang') or 'und'
    title = s.get('title') or ''
    codec = (s.get('codec') or 'unknown').upper()
    title_str = f' — "{title}"' if title else ''
    return f'Субтитры #{idx + 1} [{lang}]{title_str} ({codec})'


def _sub_codec_to_ext(codec_name):
    c = (codec_name or '').lower()
    if 'ass' in c or 'ssa' in c:
        return '.ass'
    elif 'subrip' in c or 'srt' in c:
        return '.srt'
    elif 'vtt' in c or 'webvtt' in c:
        return '.vtt'
    elif 'hdmv_pgs' in c or 'pgs' in c:
        return '.sup'
    return '.ass'


def _find_external_subtitle_sources(base_dir='.'):
    """Ищет внешние файлы субтитров в текущей папке и в подпапках."""
    sources = {}
    # 1. Текущая папка
    cur_subs = []
    for entry in os.listdir(base_dir):
        full = os.path.join(base_dir, entry)
        if os.path.isfile(full) and entry.lower().endswith(SUBTITLE_EXTENSIONS):
            cur_subs.append(full)
    if cur_subs:
        sources['[Текущая папка]'] = sorted(cur_subs)

    # 2. Подпапки (до 2 уровней вложенности)
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('bin', 'tools', '__pycache__')]
        if os.path.abspath(root) == os.path.abspath(base_dir):
            continue
        rel = os.path.relpath(root, base_dir)
        if rel.count(os.sep) > 2:
            continue
        subs = [os.path.join(root, f) for f in files if f.lower().endswith(SUBTITLE_EXTENSIONS)]
        if subs:
            sources[rel] = sorted(subs)

    return sources


def _find_external_fonts(base_dir='.'):
    """Ищет файлы шрифтов в подпапках Fonts/ и текущей директории."""
    fonts = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('bin', 'tools', '__pycache__')]
        for f in files:
            if f.lower().endswith(FONT_EXTENSIONS):
                fonts.append(os.path.join(root, f))
    return sorted(fonts)


def _extract_episode_Tokens(filename):
    """Извлекает номера из имени файла для сопоставления видео и внешней озвучки."""
    name, _ = os.path.splitext(os.path.basename(filename))
    parsed = parse_filename(filename)
    if parsed:
        ep, sn, sid = parsed
        return ('parsed', int(sn), int(ep), str(sid))

    ignore_set = {'1080', '720', '480', '2160', '264', '265'}
    bit_re = re.compile(r'\b(10-?bit|8-?bit|x264|x265|hevc|avc|aac|ac3|flac|mp3|5\.1|2\.0)\b', re.IGNORECASE)
    name_clean = bit_re.sub('', name)
    nums = [int(n) for n in re.findall(r'\d+', name_clean) if n not in ignore_set and int(n) < 1900]
    return ('nums', tuple(nums), name.lower())


def _find_external_audio_sources(base_dir='.'):
    """Ищет внешние аудиофайлы в текущей папке и в подпапках (озвучках)."""
    sources = {}
    # 1. Текущая папка
    cur_audios = []
    for entry in os.listdir(base_dir):
        full = os.path.join(base_dir, entry)
        if os.path.isfile(full) and entry.lower().endswith(AUDIO_EXTENSIONS):
            cur_audios.append(full)
    if cur_audios:
        sources['[Текущая папка]'] = sorted(cur_audios)

    # 2. Подпапки (до 2 уровней вложенности, исключая служебные)
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('bin', 'tools', '__pycache__')]
        if os.path.abspath(root) == os.path.abspath(base_dir):
            continue
        rel = os.path.relpath(root, base_dir)
        if rel.count(os.sep) > 2:
            continue
        auds = [os.path.join(root, f) for f in files if f.lower().endswith(AUDIO_EXTENSIONS)]
        if auds:
            sources[rel] = sorted(auds)

    return sources


def _match_videos_with_audios(video_files, audio_files):
    """Сопоставляет список видеофайлов со списком внешних аудиофайлов."""
    matched = {}
    unused_audios = list(audio_files)

    # Шаг 1: Точное совпадение имени без расширения
    audio_by_stem = {}
    for a in unused_audios:
        stem = os.path.splitext(os.path.basename(a))[0].lower()
        audio_by_stem.setdefault(stem, []).append(a)

    for v in video_files:
        v_stem = os.path.splitext(os.path.basename(v))[0].lower()
        if v_stem in audio_by_stem and audio_by_stem[v_stem]:
            chosen = audio_by_stem[v_stem].pop(0)
            matched[v] = chosen
            unused_audios.remove(chosen)

    # Шаг 2: Сопоставление по распарсенному (сезон, серия) или номеру серии
    unmatched_videos = [v for v in video_files if v not in matched]
    if unmatched_videos and unused_audios:
        for v in list(unmatched_videos):
            v_tok = _extract_episode_Tokens(v)
            for a in list(unused_audios):
                a_tok = _extract_episode_Tokens(a)
                if v_tok[0] == 'parsed' and a_tok[0] == 'parsed':
                    if v_tok[1] == a_tok[1] and v_tok[2] == a_tok[2]:
                        matched[v] = a
                        unused_audios.remove(a)
                        unmatched_videos.remove(v)
                        break
                elif v_tok[0] == 'parsed' and a_tok[0] == 'nums' and a_tok[1]:
                    if v_tok[2] in a_tok[1]:
                        matched[v] = a
                        unused_audios.remove(a)
                        unmatched_videos.remove(v)
                        break
                elif v_tok[0] == 'nums' and a_tok[0] == 'nums' and v_tok[1] and a_tok[1]:
                    if v_tok[1][0] == a_tok[1][0]:
                        matched[v] = a
                        unused_audios.remove(a)
                        unmatched_videos.remove(v)
                        break

    # Шаг 3: Если количество оставшихся совпадает 1-в-1 по сортировке
    if unmatched_videos and len(unmatched_videos) == len(unused_audios):
        for v, a in zip(sorted(unmatched_videos), sorted(unused_audios)):
            matched[v] = a

    return matched


def _prompt_audio_plan(files, ffprobe, ffmpeg):
    """
    Интерактивный выбор режима работы с озвучками перед обработкой пачки видео.
    Возвращает словарь audio_plan:
      - mode: 'all' | 'single_internal' | 'reorder_internal' | 'external_replace' | 'external_first'
      - track_idx: int (индекс выбранной встроенной дорожки)
      - external_map: dict {video_file: external_audio_file}
      - voice_title: str (название выбранной озвучки, если есть)
    """
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    audios = info.get('audio') or []
    ext_sources = _find_external_audio_sources('.')

    print(f'\n{CYAN}{BOLD}─── Настройка озвучки (анализ по {os.path.basename(first)}) ───{RESET}')
    if audios:
        print(f'{BOLD}Встроенные аудиодорожки в контейнере:{RESET}')
        for a in audios:
            print(f'  {GREEN}{a["a_idx"] + 1}{RESET}. {_format_audio_desc(a)}')
    else:
        print(f'  {YELLOW}Встроенных аудиодорожек не обнаружено.{RESET}')

    if ext_sources:
        print(f'\n{BOLD}Найденные внешние файлы озвучки:{RESET}')
        for folder, flist in ext_sources.items():
            print(f'  {MAGENTA}• {folder}{RESET}: {len(flist)} аудиофайл(ов) ({os.path.basename(flist[0])}...)')

    print(f'\n{BOLD}Режим работы со звуком:{RESET}')
    print(f'  {GREEN}1{RESET}. Оставить все встроенные дорожки как есть {DIM}(по умолчанию){RESET}')
    if len(audios) >= 1:
        print(f'  {GREEN}2{RESET}. Оставить ТОЛЬКО ОДНУ встроенную озвучку {DIM}(рекомендуется для Telegram){RESET}')
    if len(audios) > 1:
        print(f'  {GREEN}3{RESET}. Сделать выбранную озвучку ПЕРВОЙ, остальные сохранить')
    if ext_sources:
        print(f'  {GREEN}4{RESET}. Вшить ВНЕШНЮЮ озвучку (.mka/.aac/.mp3) ВМЕСТО встроенного звука')
        print(f'  {GREEN}5{RESET}. Вшить ВНЕШНЮЮ озвучку ПЕРВОЙ дорожкой + сохранить встроенные')

    choice = input(f'\n{BOLD}Выбор режима [Enter = 1]: {RESET}').strip() or '1'

    if choice == '2' and audios:
        if len(audios) == 1:
            idx = 0
        else:
            raw = input(f'{BOLD}Номер дорожки [1-{len(audios)}]: {RESET}').strip()
            idx = (int(raw) - 1) if raw.isdigit() and 1 <= int(raw) <= len(audios) else 0
        chosen = audios[idx]
        print(f'  {GREEN}✓ Выбрана только: {_format_audio_desc(chosen)}{RESET}')
        return {
            'mode': 'single_internal',
            'track_idx': idx,
            'external_map': {},
            'voice_title': chosen.get('title') or '',
        }

    if choice == '3' and len(audios) > 1:
        raw = input(f'{BOLD}Какую дорожку поставить первой [1-{len(audios)}]: {RESET}').strip()
        idx = (int(raw) - 1) if raw.isdigit() and 1 <= int(raw) <= len(audios) else 0
        chosen = audios[idx]
        print(f'  {GREEN}✓ Первой дорожкой будет: {_format_audio_desc(chosen)}{RESET}')
        return {
            'mode': 'reorder_internal',
            'track_idx': idx,
            'external_map': {},
            'voice_title': chosen.get('title') or '',
        }

    if choice in ('4', '5') and ext_sources:
        folders = list(ext_sources.keys())
        if len(folders) == 1:
            chosen_folder = folders[0]
        else:
            print(f'\n{BOLD}Выберите папку с внешней озвучкой:{RESET}')
            for i, fld in enumerate(folders, 1):
                print(f'  {GREEN}{i}{RESET}. {fld} ({len(ext_sources[fld])} файлов)')
            f_raw = input(f'{BOLD}Номер папки [1-{len(folders)}]: {RESET}').strip()
            f_idx = (int(f_raw) - 1) if f_raw.isdigit() and 1 <= int(f_raw) <= len(folders) else 0
            chosen_folder = folders[f_idx]

        ext_files = ext_sources[chosen_folder]
        ext_map = _match_videos_with_audios(files, ext_files)
        print(f'\n{CYAN}Сопоставлено файлов: {len(ext_map)}/{len(files)}{RESET}')
        for v in files[:8]:
            a_match = ext_map.get(v)
            a_name = os.path.basename(a_match) if a_match else 'НЕ НАЙДЕНО'
            color = GREEN if a_match else RED
            print(f'  {DIM}{os.path.basename(v)}{RESET}  <--  {color}{a_name}{RESET}')
        if len(files) > 8:
            print(f'  {DIM}... и ещё {len(files) - 8}{RESET}')

        return {
            'mode': 'external_replace' if choice == '4' else 'external_first',
            'track_idx': 0,
            'external_map': ext_map,
            'voice_title': chosen_folder if chosen_folder != '[Текущая папка]' else '',
        }

    return {
        'mode': 'all',
        'track_idx': 0,
        'external_map': {},
        'voice_title': audios[0].get('title', '') if audios else '',
    }


def _build_ffmpeg_audio_mapping(f, info, audio_plan, ffprobe, ffmpeg, aac_bitrate='192k'):
    """
    Формирует списки inputs, maps, a_args для ffmpeg на основе audio_plan.
    """
    mode = (audio_plan or {}).get('mode', 'all')
    track_idx = (audio_plan or {}).get('track_idx', 0)
    ext_map = (audio_plan or {}).get('external_map') or {}
    audios = (info or {}).get('audio') or []

    inputs = ['-i', f]
    maps = ['-map', '0:v:0']
    out_audio_streams = []

    ext_audio = ext_map.get(f)
    if mode in ('external_replace', 'external_first') and ext_audio and os.path.exists(ext_audio):
        inputs += ['-i', ext_audio]
        ext_info = _probe_all(ext_audio, ffprobe, ffmpeg)
        ext_audios = ext_info.get('audio') or [{'codec': 'unknown', 'bitrate': None}]
        maps += ['-map', '1:a:0']
        out_audio_streams.append(ext_audios[0])

        if mode == 'external_first' and audios:
            for a in audios:
                maps += ['-map', f'0:a:{a["a_idx"]}']
                out_audio_streams.append(a)
    elif mode == 'single_internal' and audios:
        chosen_idx = track_idx if 0 <= track_idx < len(audios) else 0
        maps += ['-map', f'0:a:{chosen_idx}']
        out_audio_streams.append(audios[chosen_idx])
    elif mode == 'reorder_internal' and len(audios) > 1:
        chosen_idx = track_idx if 0 <= track_idx < len(audios) else 0
        maps += ['-map', f'0:a:{chosen_idx}']
        out_audio_streams.append(audios[chosen_idx])
        for a in audios:
            if a['a_idx'] != chosen_idx:
                maps += ['-map', f'0:a:{a["a_idx"]}']
                out_audio_streams.append(a)
    else:
        if audios:
            maps += ['-map', '0:a?']
            out_audio_streams.extend(audios)
        else:
            maps += ['-an']

    a_args = []
    for i, a in enumerate(out_audio_streams):
        if a.get('codec') == 'aac' and (a.get('channels') or 2) <= 2:
            a_args += [f'-c:a:{i}', 'copy']
        else:
            a_args += [f'-c:a:{i}', 'aac',
                       f'-b:a:{i}', aac_bitrate,
                       f'-ac:a:{i}', '2']

    return inputs, maps, a_args, out_audio_streams


# ============================================================================
#  FASTSTART
# ============================================================================
def _has_faststart(path):
    try:
        with open(path, 'rb') as fh:
            head = fh.read(8 * 1024 * 1024)
        moov = head.find(b'moov')
        mdat = head.find(b'mdat')
        if moov == -1:
            return False
        if mdat == -1:
            return True
        return moov < mdat
    except OSError:
        return False


def _ensure_faststart(src_path, ffmpeg):
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in ('.mp4', '.m4v', '.mov'):
        return True, 'skip (не mp4)'
    if _has_faststart(src_path):
        return True, 'уже есть'
    return _ensure_faststart_ffmpeg(src_path, ffmpeg)


def _ensure_faststart_ffmpeg(src_path, ffmpeg):
    name, ext_orig = os.path.splitext(src_path)
    tmp = f'{name}.faststart.tmp{ext_orig}'
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    try:
        r = subprocess.run(
            [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
             '-i', src_path, '-c', 'copy', '-sn', '-dn',
             '-map_metadata', '0', '-movflags', '+faststart',
             '-threads', '0', tmp],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, encoding='utf-8',
            errors='ignore', timeout=3600)
    except subprocess.TimeoutExpired:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, 'ffmpeg timeout'

    if r.returncode != 0 or not os.path.exists(tmp):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, f'ffmpeg rc={r.returncode}'

    try:
        os.remove(src_path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, f'не удалить оригинал: {e}'
    try:
        os.replace(tmp, src_path)
    except OSError as e:
        return False, f'rename: {e}'

    try:
        _probe_all_cached.cache_clear()
    except Exception:
        pass
    return True, 'применён (ffmpeg)'


# ============================================================================
#  БЛОК 1 — TELEGRAM UPLOADER (TDLib)
# ============================================================================
def _load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default if default is not None else {}
    return default if default is not None else {}


def _save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'{RED}Ошибка сохранения {path}: {e}{RESET}')


def load_config():
    defaults = {
        "channel": "",
        "videos_dir": os.getcwd(),
        "invite_link": INVITE_LINK,
        "api_id": API_ID,
        "api_hash": API_HASH,
        "notify_bot_token": "",
        "notify_chat_id": "",
    }
    global_cfg = os.path.join(DEFAULT_TOOLKIT_DIR, "config.json")
    if os.path.exists(global_cfg):
        try:
            with open(global_cfg, "r", encoding='utf-8') as f:
                defaults.update(json.load(f))
        except Exception:
            pass

    if os.path.exists(CONFIG_FILE) and os.path.abspath(CONFIG_FILE) != os.path.abspath(global_cfg):
        try:
            with open(CONFIG_FILE, "r", encoding='utf-8') as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults


def save_config(cfg):
    _save_json(CONFIG_FILE, cfg)
    global_cfg = os.path.join(DEFAULT_TOOLKIT_DIR, "config.json")
    if os.path.abspath(CONFIG_FILE) != os.path.abspath(global_cfg):
        _save_json(global_cfg, cfg)


def clean_path_input(raw: str) -> str:
    """Очищает путь от кавычек и лишних пробелов при drag-and-drop или копировании пути."""
    if not raw:
        return ""
    s = raw.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return os.path.normpath(s)


def search_shikimori_animes(query: str, limit: int = 5):
    """Поиск аниме по названию через Shikimori API с поддержкой зеркал."""
    import requests
    urls = [
        "https://shikimori.io/api/animes",
        "https://shikimori.me/api/animes",
        "https://shikimori.one/api/animes",
    ]
    headers = {"User-Agent": "TelegramAnimeUploader/2.0"}
    for url in urls:
        try:
            resp = requests.get(url, params={"search": query, "limit": limit}, headers=headers, timeout=4)
            if resp.status_code == 200:
                results = resp.json()
                if results and isinstance(results, list):
                    return results
        except Exception:
            continue
    return []


def get_shikimori_title(shikimori_id: str):
    import requests
    urls = [
        f"https://shikimori.io/api/animes/{shikimori_id}",
        f"https://shikimori.me/api/animes/{shikimori_id}",
        f"https://shikimori.one/api/animes/{shikimori_id}",
    ]
    headers = {"User-Agent": "TelegramAnimeUploader/2.0"}
    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                title = data.get("russian") or data.get("name")
                if title:
                    return title
        except Exception:
            continue
    return None


def make_hashtag(title: str) -> str:
    if not title:
        return "#Anime"
    title_clean = title.replace("-", " ").replace("—", " ").replace("–", " ")
    cleaned = re.sub(r'[^\w\s]', '', title_clean, flags=re.UNICODE)
    words = [w for w in cleaned.split() if w]
    if not words:
        return "#Anime"
    return "#" + "_".join(words)


def parse_filename(filename: str):
    name_no_ext = os.path.splitext(filename)[0]

    pattern = re.compile(
        r'\[(?:[Ee])?(\d+)\]_'
        r'\[(?:[Ss])?(\d+)\]_'
        r'\[?(\d+)\]?'
    )
    m = pattern.search(name_no_ext)
    if m:
        ep, sn, sid = m.groups()
        return int(ep), int(sn), sid

    pattern2 = re.compile(
        r'(?:^|[^A-Za-z0-9])'
        r'(?:[Ee])?(\d+)'
        r'[_\-\s]+'
        r'(?:[Ss])?(\d+)'
        r'[_\-\s]+'
        r'(\d+)'
        r'(?:$|[^0-9])'
    )
    m = pattern2.search(name_no_ext)
    if m:
        ep, sn, sid = m.groups()
        return int(ep), int(sn), sid

    pattern3 = re.compile(
        r'\[(?:[Ee])?(\d+)'
        r'[_\-\s]+'
        r'(?:[Ss])?(\d+)\]'
        r'[_\-\s]+'
        r'(\d+)'
    )
    m = pattern3.search(name_no_ext)
    if m:
        ep, sn, sid = m.groups()
        return int(ep), int(sn), sid

    return None


def parse_channel_input(channel_str: str):
    channel_str = str(channel_str).strip()
    if not channel_str:
        return None
    if channel_str.lstrip('-').isdigit():
        return int(channel_str)
    if not channel_str.startswith("@") and not channel_str.startswith("http"):
        return f"@{channel_str}"
    return channel_str


def _generate_thumbnail(ffmpeg, input_path, output_path,
                        time_offset=None, width=320, duration=0):
    if time_offset is None:
        if duration and duration > 30:
            time_offset = min(180, max(5, int(duration * 0.15)))
        elif duration and duration > 5:
            time_offset = 3
        else:
            time_offset = 1

    cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
           '-threads', '2', '-nostdin',
           '-ss', str(time_offset),
           '-i', input_path,
           '-an', '-sn', '-vframes', '1',
           '-vf', f'scale={width}:-2',
           '-q:v', '4', output_path]
    try:
        r = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='ignore',
            timeout=THUMB_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f'thumbnail timeout ({THUMB_TIMEOUT_SEC}s) для '
            f'{os.path.basename(input_path)}')
    if r.returncode != 0:
        err = (r.stderr or '')[-300:]
        raise RuntimeError(f'ffmpeg thumb rc={r.returncode}: {err}')
    if not os.path.exists(output_path):
        raise RuntimeError('thumbnail не создан')


def _thumb_path_for(video_path, thumb_dir):
    h = hashlib.md5(video_path.encode('utf-8')).hexdigest()[:16]
    return os.path.join(thumb_dir, f'{h}.jpg')


class _ProgressState:
    def __init__(self, total_bytes):
        self.total = total_bytes or 0
        self.done = 0
        self.start = time.time()
        self.lock = threading.Lock()

    def update(self, uploaded):
        with self.lock:
            self.done = uploaded

    def snapshot(self):
        with self.lock:
            return self.done, self.total, self.start


def _spin_tdlib_progress(state: _ProgressState, label: str, stop_ev: threading.Event):
    chars = '|/-\\'
    k = 0
    while not stop_ev.wait(0.25):
        done, total, start = state.snapshot()
        elapsed = time.time() - start
        if total > 0 and done > 0:
            pct = done / total * 100
            speed = done / (1024 * 1024) / elapsed if elapsed > 0 else 0
            eta = (total - done) / (done / elapsed) if done and elapsed > 0 else 0
            bar = _make_bar(pct, 28)
            line = (f'\r  {CYAN}{bar}{RESET} {pct:5.1f}%  '
                    f'{done/(1024*1024):7.1f}/{total/(1024*1024):.1f} MB  '
                    f'{speed:6.1f} MB/s  ETA {_fmt_time(eta)}  ')
        else:
            line = f'\r  {DIM}[*] отправка {chars[k % 4]}{RESET}  {label[:50]:<50}'
        sys.stdout.write(line + '   ')
        sys.stdout.flush()
        k += 1
    sys.stdout.write('\r' + ' ' * 90 + '\r')
    sys.stdout.flush()


def _prepare_video_item(item, ffmpeg, ffprobe, thumb_dir):
    path = item['filepath']
    result = {
        'video_path': path,
        'thumb_path': None,
        'width': None,
        'height': None,
        'duration': None,
        'is_cached': False,
    }

    try:
        info = _get_video_info(path, ffprobe, ffmpeg)
        if info:
            result['width'] = info['width']
            result['height'] = info['height']
            result['duration'] = info['duration']
    except Exception:
        pass

    try:
        thumb = _thumb_path_for(path, thumb_dir)
        if os.path.exists(thumb):
            result['thumb_path'] = thumb
            result['is_cached'] = True
        elif ffmpeg and _have(ffmpeg):
            _generate_thumbnail(ffmpeg, path, thumb, duration=result.get('duration') or 0)
            result['thumb_path'] = thumb
    except Exception:
        pass

    if FASTSTART_ON_UPLOAD:
        try:
            size_mb = os.path.getsize(path) / (1024 * 1024)
        except OSError:
            size_mb = 0

        if not (size_mb and size_mb > FASTSTART_MAX_MB):
            try:
                if not _has_faststart(path):
                    _ensure_faststart(path, ffmpeg)
            except Exception:
                pass

    return result


def _prep_worker_main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if req.get('cmd') == 'stop':
            break
        if req.get('cmd') != 'prepare':
            continue
        result_path = req.get('result_path')
        try:
            result = _prepare_video_item(
                req['item'], req['ffmpeg'], req['ffprobe'], req['thumb_dir']
            )
            payload = {'ok': True, 'result': result}
        except Exception as e:
            payload = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        if result_path:
            tmp = result_path + '.tmp'
            try:
                with open(tmp, 'w', encoding='utf-8') as fh:
                    json.dump(payload, fh, ensure_ascii=False)
                os.replace(tmp, result_path)
            except Exception:
                try:
                    with open(result_path, 'w', encoding='utf-8') as fh:
                        json.dump(payload, fh, ensure_ascii=False)
                except Exception:
                    pass


class _PrepWorkerClient:
    def __init__(self, ffmpeg, ffprobe, thumb_dir):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.thumb_dir = thumb_dir
        self.proc = None
        self.result_dir = tempfile.mkdtemp(prefix='anime_prep_')
        self.counter = 0

        # Корректный запуск воркера как из .py, так и из собранного .exe (PyInstaller)
        if getattr(sys, 'frozen', False):
            worker_cmd = [sys.executable, '--__anime_prep_worker']
        else:
            worker_cmd = [sys.executable, os.path.abspath(__file__), '--__anime_prep_worker']

        self.proc = subprocess.Popen(
            worker_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='ignore',
            bufsize=1,
        )

    def request(self, item):
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError('Prep-worker неожиданно завершился')
        self.counter += 1
        job_id = f'{os.getpid()}_{self.counter}_{int(time.time()*1000)}'
        result_path = os.path.join(self.result_dir, f'{job_id}.json')
        req = {
            'cmd': 'prepare',
            'result_path': result_path,
            'item': item,
            'ffmpeg': self.ffmpeg,
            'ffprobe': self.ffprobe,
            'thumb_dir': self.thumb_dir,
        }
        try:
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + '\n')
            self.proc.stdin.flush()
        except Exception as e:
            raise RuntimeError(f'Не удалось передать задачу prep-worker: {e}') from e
        return result_path

    async def wait(self, result_path, timeout=21600):
        deadline = time.time() + timeout
        while True:
            if os.path.exists(result_path):
                try:
                    with open(result_path, 'r', encoding='utf-8') as fh:
                        payload = json.load(fh)
                finally:
                    try:
                        os.remove(result_path)
                    except OSError:
                        pass
                if not payload.get('ok'):
                    raise RuntimeError(payload.get('error', 'prep-worker error'))
                return payload['result']
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f'Prep-worker завершился с кодом {self.proc.returncode}')
            if time.time() >= deadline:
                raise TimeoutError('Таймаут ожидания prep-worker')
            await asyncio.sleep(0.20)

    def close(self):
        proc = self.proc
        self.proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(json.dumps({'cmd': 'stop'}) + '\n')
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        try:
            shutil.rmtree(self.result_dir, ignore_errors=True)
        except Exception:
            pass


# ============================================================================
#  TDJSON DIRECT CLIENT
# ============================================================================
class _TDJsonDirect:
    def __init__(self, session_dir):
        try:
            import tdjson
        except ImportError as e:
            raise RuntimeError(
                'Не найден пакет tdjson. Установи: pip install tdjson'
            ) from e

        self.tdjson = tdjson

        tdjson_ver = str(getattr(tdjson, '__version__', 'unknown'))
        if tdjson_ver.startswith(('1.8.65', '1.8.66', '1.8.67')):
            raise RuntimeError(
                'Установлена tdjson %s. Нужна tdjson 1.8.64.post1.\n'
                'Выполни: python -m pip install --force-reinstall --no-cache-dir tdjson==1.8.64.post1'
                % tdjson_ver
            )

        try:
            tdjson.td_execute(json.dumps({
                "@type": "setLogVerbosityLevel",
                "new_verbosity_level": 1,
            }).encode("utf-8"))
        except Exception:
            pass
        self.session_dir = os.path.abspath(session_dir)
        os.makedirs(self.session_dir, exist_ok=True)

        self.client_id = int(self.tdjson.td_create_client_id())

        self.loop = None
        self.recv_thread = None
        self.stop_event = threading.Event()
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.auth_queue = None

        self.send_waiters = {}
        self.send_results = {}
        self.send_lock = threading.Lock()
        self.file_progress = {}
        self.progress_lock = threading.Lock()

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.auth_queue = asyncio.Queue()
        self.stop_event.clear()
        self.recv_thread = threading.Thread(
            target=self._receive_loop,
            name='TDJsonReceive',
            daemon=True,
        )
        self.recv_thread.start()

    def _receive_loop(self):
        td_receive = self.tdjson.td_receive
        while not self.stop_event.is_set():
            try:
                raw = td_receive(1.0)
            except Exception as e:
                if not self.stop_event.is_set():
                    self._threadsafe_put_auth({
                        '@type': '__tdjson_internal_error',
                        'message': f'{type(e).__name__}: {e}',
                    })
                continue

            if not raw:
                continue

            try:
                if isinstance(raw, bytes):
                    raw = raw.decode('utf-8', errors='replace')
                update = json.loads(raw)
            except Exception:
                continue

            self._dispatch(update)

    def _threadsafe_put_auth(self, update):
        if self.loop is not None and self.auth_queue is not None:
            self.loop.call_soon_threadsafe(self.auth_queue.put_nowait, update)

    def _dispatch(self, update):
        extra = update.get('@extra')
        if extra is not None:
            key = None
            if isinstance(extra, dict):
                key = extra.get('request_id') or extra.get('id')
            else:
                key = str(extra)

            if key is not None:
                with self.pending_lock:
                    fut = self.pending.pop(str(key), None)
                if fut is not None and self.loop is not None and not fut.done():
                    self.loop.call_soon_threadsafe(fut.set_result, update)

        typ = update.get('@type')

        if typ == 'updateAuthorizationState':
            self._threadsafe_put_auth(update)

        elif typ == 'updateMessageSendSucceeded':
            old_id = str(update.get('old_message_id'))
            with self.send_lock:
                waiter = self.send_waiters.pop(old_id, None)
                if waiter is None:
                    self.send_results[old_id] = {
                        'ok': True,
                        'update': update,
                    }
            if waiter is not None and self.loop is not None:
                self.loop.call_soon_threadsafe(waiter.set_result, {
                    'ok': True,
                    'update': update,
                })

        elif typ == 'updateMessageSendFailed':
            old_id = str(update.get('old_message_id'))
            error = update.get('error') or {}
            result = {
                'ok': False,
                'code': error.get('code'),
                'message': error.get('message') or 'Неизвестная ошибка Telegram',
                'update': update,
            }
            with self.send_lock:
                waiter = self.send_waiters.pop(old_id, None)
                if waiter is None:
                    self.send_results[old_id] = result
            if waiter is not None and self.loop is not None:
                self.loop.call_soon_threadsafe(waiter.set_result, result)

        elif typ == 'updateFile':
            f = update.get('file') or {}
            fid = f.get('id')
            if fid is not None:
                remote = f.get('remote') or {}
                local = f.get('local') or {}
                size = int(f.get('size') or 0)
                uploaded = int(
                    remote.get('uploaded_size')
                    or local.get('uploaded_size')
                    or 0
                )
                with self.progress_lock:
                    self.file_progress[int(fid)] = {
                        'size': size,
                        'uploaded': uploaded,
                        'active': bool(remote.get('is_uploading_active')),
                        'completed': bool(remote.get('is_uploading_completed')),
                    }

    async def request(self, request, timeout=60):
        if self.loop is None:
            raise RuntimeError('TDJson client не запущен')

        request = dict(request)
        request_id = f'{os.getpid()}-{time.time_ns()}'
        request['@extra'] = {'request_id': request_id}
        fut = self.loop.create_future()
        with self.pending_lock:
            self.pending[request_id] = fut

        payload = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        try:
            self.tdjson.td_send(self.client_id, payload)
        except Exception:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise

    async def wait_auth_state(self, timeout=600):
        return await asyncio.wait_for(self.auth_queue.get(), timeout=timeout)

    async def send_video_and_wait(self, chat_id, content, progress_state,
                                  timeout=6 * 3600):
        result = await self.request({
            '@type': 'sendMessage',
            'chat_id': int(chat_id),
            'input_message_content': content,
        }, timeout=120)

        if result.get('@type') == 'error':
            raise RuntimeError(
                f"TDLib error {result.get('code')}: {result.get('message')}"
            )

        if result.get('@type') != 'message':
            raise RuntimeError(f"Неожиданный ответ sendMessage: {result.get('@type')}")

        old_message_id = str(result.get('id'))
        video = (((result.get('content') or {}).get('video') or {}).get('video') or {})
        file_id = video.get('id')

        if file_id is not None:
            with self.progress_lock:
                cached = self.file_progress.get(int(file_id))
            if cached:
                progress_state.update(cached.get('uploaded', 0))

        waiter = None
        with self.send_lock:
            cached_result = self.send_results.pop(old_message_id, None)
            if cached_result is None:
                waiter = self.loop.create_future()
                self.send_waiters[old_message_id] = waiter

        if cached_result is None:
            deadline = time.time() + timeout
            while not waiter.done():
                if file_id is not None:
                    prog = self.get_progress(file_id)
                    if prog:
                        total = prog.get('size') or progress_state.total
                        if total and progress_state.total != total:
                            progress_state.total = total
                        progress_state.update(prog.get('uploaded', 0))
                remaining = deadline - time.time()
                if remaining <= 0:
                    with self.send_lock:
                        self.send_waiters.pop(old_message_id, None)
                    raise RuntimeError(
                        'Telegram не подтвердил отправку за отведённое время.'
                    )
                try:
                    await asyncio.wait_for(asyncio.shield(waiter), timeout=min(0.5, remaining))
                except asyncio.TimeoutError:
                    pass
            cached_result = waiter.result()

        if not cached_result.get('ok'):
            raise RuntimeError(
                f"Telegram отклонил отправку: {cached_result.get('code')} — "
                f"{cached_result.get('message')}"
            )

        if file_id is not None:
            with self.progress_lock:
                cached = self.file_progress.get(int(file_id))
            if cached:
                progress_state.update(cached.get('uploaded', 0))

        return cached_result.get('update')

    def get_progress(self, file_id):
        if file_id is None:
            return None
        with self.progress_lock:
            p = self.file_progress.get(int(file_id))
        return dict(p) if p else None

    def stop(self):
        self.stop_event.set()
        try:
            self.tdjson.td_send(
                self.client_id,
                json.dumps({'@type': 'close'}).encode('utf-8')
            )
        except Exception:
            pass
        if self.recv_thread is not None and self.recv_thread.is_alive():
            self.recv_thread.join(timeout=5)


def _print_qr_in_console(link: str):
    """Рисует QR-код в консоли (если есть библиотека qrcode) и даёт ссылку."""
    print(f'\n  {CYAN}{BOLD}═══ ВХОД ПО QR-КОДУ ═══{RESET}')
    print(f'  1. Открой Telegram на телефоне')
    print(f'  2. Перейди в {BOLD}Настройки -> Устройства -> Подключить устройство{RESET}')
    print(f'  3. Отсканируй QR-код ниже:\n')
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(link)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        print(f'  {YELLOW}[!] Для отрисовки QR прямо в консоли установи: pip install qrcode{RESET}')
        print(f'  {GREEN}Или открой эту ссылку в браузере для показа QR:{RESET}')
        print(f'  https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={link}\n')
    print(f'  {DIM}Прямая ссылка: {link}{RESET}\n')


async def _authorize_tdjson(td: _TDJsonDirect, api_id, api_hash):
    auth_mode = None  # 'qr', 'bot', 'phone'

    while True:
        update = await td.wait_auth_state(timeout=600)
        typ = update.get('@type')

        if typ == '__tdjson_internal_error':
            raise RuntimeError(update.get('message', 'Ошибка td_receive'))

        if typ != 'updateAuthorizationState':
            continue

        state = update.get('authorization_state') or {}
        state_type = state.get('@type', '')

        if state_type == 'authorizationStateWaitTdlibParameters':
            res = await td.request({
                '@type': 'setTdlibParameters',
                'use_test_dc': False,
                'database_directory': td.session_dir,
                'files_directory': os.path.join(td.session_dir, 'files'),
                'database_encryption_key': '',
                'use_file_database': True,
                'use_chat_info_database': True,
                'use_message_database': True,
                'use_secret_chats': False,
                'api_id': int(api_id),
                'api_hash': str(api_hash),
                'system_language_code': 'ru',
                'device_model': 'AnimeToolkit PC',
                'system_version': 'Windows 10',
                'application_version': '8.0',
                'enable_storage_optimizer': True,
                'ignore_file_names': False,
            }, timeout=120)
            if res.get('@type') == 'error':
                raise RuntimeError(f"setTdlibParameters: {res.get('code')} — {res.get('message')}")

        elif state_type == 'authorizationStateWaitEncryptionKey':
            res = await td.request({
                '@type': 'checkDatabaseEncryptionKey',
                'encryption_key': ''
            }, timeout=120)
            if res.get('@type') == 'error':
                raise RuntimeError(f"checkDatabaseEncryptionKey: {res.get('code')} — {res.get('message')}")

        elif state_type == 'authorizationStateWaitPhoneNumber':
            print(f'\n  {CYAN}{BOLD}Выберите способ авторизации в Telegram:{RESET}')
            print(f'    {GREEN}1{RESET}. По QR-коду с телефона {GREEN}(Рекомендуется — без СМС и кодов!){RESET}')
            print(f'    {GREEN}2{RESET}. Через токен Бота (@BotFather) {DIM}(заливка до 2 ГБ от имени бота){RESET}')
            print(f'    {GREEN}3{RESET}. По номеру телефона {DIM}(обычный вход по коду){RESET}')
            choice = input(f'\n  {BOLD}Способ входа [1-3, Enter = 1]: {RESET}').strip() or '1'

            if choice == '1':
                auth_mode = 'qr'
                res = await td.request({
                    '@type': 'requestQrCodeAuthentication',
                    'other_user_ids': [],
                }, timeout=120)
                if res.get('@type') == 'error':
                    raise RuntimeError(f"requestQrCodeAuthentication: {res.get('code')} — {res.get('message')}")

            elif choice == '2':
                auth_mode = 'bot'
                token = input('  Вставьте токен бота (от @BotFather): ').strip()
                res = await td.request({
                    '@type': 'checkAuthenticationBotToken',
                    'token': token,
                }, timeout=120)
                if res.get('@type') == 'error':
                    raise RuntimeError(f"checkAuthenticationBotToken: {res.get('code')} — {res.get('message')}")

            else:
                auth_mode = 'phone'
                phone = input('  Номер телефона (например +79991234567): ').strip()
                res = await td.request({
                    '@type': 'setAuthenticationPhoneNumber',
                    'phone_number': phone,
                    'settings': {
                        '@type': 'phoneNumberAuthenticationSettings',
                        'allow_flash_call': False,
                        'allow_missed_call': False,
                        'is_current_phone_number': False,
                        'allow_sms_retriever_api': False,
                    },
                }, timeout=120)
                if res.get('@type') == 'error':
                    raise RuntimeError(f"setAuthenticationPhoneNumber: {res.get('code')} — {res.get('message')}")

        elif state_type == 'authorizationStateWaitOtherDeviceConfirmation':
            link = (state.get('link') or '').strip()
            _print_qr_in_console(link)
            print(f'  {YELLOW}[*] Ожидание сканирования QR-кода на телефоне...{RESET}')

        elif state_type == 'authorizationStateWaitCode':
            code_info = state.get('code_info') or {}
            delivery_type = (code_info.get('type') or {}).get('@type', '')
            print(f'  {DIM}Тип доставки кода: {delivery_type}{RESET}')
            code = input('  Код из Telegram (или введи r для повторной отправки по СМС): ').strip()
            if code.lower() == 'r':
                await td.request({'@type': 'resendAuthenticationCode'}, timeout=60)
                print(f'  {GREEN}Запрошена повторная отправка кода...{RESET}')
                continue
            res = await td.request({
                '@type': 'checkAuthenticationCode',
                'code': code,
            }, timeout=120)
            if res.get('@type') == 'error':
                raise RuntimeError(f"checkAuthenticationCode: {res.get('code')} — {res.get('message')}")

        elif state_type == 'authorizationStateWaitPassword':
            hint = state.get('password_hint', '')
            hint_str = f' (подсказка: {hint})' if hint else ''
            pwd = input(f'  Пароль 2FA (облачный пароль){hint_str}: ').strip()
            res = await td.request({
                '@type': 'checkAuthenticationPassword',
                'password': pwd,
            }, timeout=120)
            if res.get('@type') == 'error':
                raise RuntimeError(f"checkAuthenticationPassword: {res.get('code')} — {res.get('message')}")

        elif state_type == 'authorizationStateWaitEmailAddress':
            email = input('  Email Telegram: ').strip()
            res = await td.request({
                '@type': 'setAuthenticationEmailAddress',
                'email_address': email,
            }, timeout=120)
            if res.get('@type') == 'error':
                raise RuntimeError(f"setAuthenticationEmailAddress: {res.get('code')} — {res.get('message')}")

        elif state_type == 'authorizationStateWaitEmailCode':
            code = input('  Код из email: ').strip()
            res = await td.request({
                '@type': 'checkAuthenticationEmailCode',
                'code': {'@type': 'emailAddressAuthenticationCode', 'code': code},
            }, timeout=120)
            if res.get('@type') == 'error':
                raise RuntimeError(f"checkAuthenticationEmailCode: {res.get('code')} — {res.get('message')}")

        elif state_type == 'authorizationStateReady':
            print(f'  {GREEN}✓ Авторизация TDLib успешна! Сессия сохранена.{RESET}')
            return

        elif state_type == 'authorizationStateClosed':
            raise RuntimeError('TDLib закрыл клиент во время авторизации.')

async def _tdlib_uploader_async():
    print(f'\n{CYAN}{BOLD}═══ Telegram Anime Uploader (TDJSON) ═══{RESET}')

    try:
        import tdjson
        print(f'{DIM}tdjson: {getattr(tdjson, "__file__", "unknown")}{RESET}')
    except ImportError:
        print(f'{RED}[!] Не найден пакет tdjson. Установи: pip install tdjson{RESET}')
        return

    ffmpeg  = get_tool_path('ffmpeg.exe'  if os.name == 'nt' else 'ffmpeg')
    ffprobe = get_tool_path('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    use_ffmpeg = _have(ffmpeg) and _have(ffprobe)
    if not use_ffmpeg:
        print(f'{YELLOW}[!] ffmpeg/ffprobe не найдены.{RESET}')

    script_dir = _get_script_dir()
    thumb_dir = os.path.join(script_dir, THUMB_CACHE_DIR)
    try:
        os.makedirs(thumb_dir, exist_ok=True)
    except Exception:
        thumb_dir = tempfile.gettempdir()

    cfg = load_config()
    default_channel = cfg.get('channel', '')
    default_invite = cfg.get('invite_link', INVITE_LINK)
    print(f'{GREEN}✓ Канал из config: {default_channel}{RESET}')
    print(f'{GREEN}✓ Инвайт: {default_invite}{RESET}')
    override = input('  Новый ID/username канала (Enter — оставить): ').strip()
    channel_input_str = override if override else default_channel

    default_dir = cfg.get('videos_dir', os.getcwd())
    videos_dir_raw = input(f'\n{BOLD}Папка с видео [{default_dir}]: {RESET}').strip()
    videos_dir = clean_path_input(videos_dir_raw) if videos_dir_raw else default_dir
    if not os.path.exists(videos_dir):
        print(f'{RED}Папка {videos_dir} не найдена.{RESET}')
        return
    cfg['videos_dir'] = videos_dir
    save_config(cfg)

    cleaned = 0
    for pat in ('*.faststart.tmp.*', '*.remux_tmp.*', '*.mkvmerge_tmp.*',
                '*.remux_stage1.mkv', '*.remux_stage2.tmp.mp4', '*.remux_direct.tmp.mp4'):
        for junk in glob.glob(os.path.join(videos_dir, pat)):
            try:
                os.remove(junk)
                cleaned += 1
            except OSError:
                pass
    if cleaned:
        print(f'{DIM}очищено мусорных файлов: {cleaned}{RESET}')

    print(f'\n{CYAN}Сканирование папки: {videos_dir}{RESET}')
    while True:
        all_files = os.listdir(videos_dir)
        video_files = [f for f in all_files if f.lower().endswith(VIDEO_EXTENSIONS)]
        parsed_items = []
        for f in video_files:
            parsed = parse_filename(f)
            if parsed:
                ep_num, season_num, shikimori_id = parsed
                parsed_items.append({
                    'filename': f,
                    'filepath': os.path.join(videos_dir, f),
                    'ep_num': ep_num, 'season_num': season_num,
                    'shikimori_id': shikimori_id,
                })
        if parsed_items:
            break

        if video_files:
            print(f'\n{YELLOW}┌────────────────── ВНИМАНИЕ: НЕВЕРНЫЙ ФОРМАТ ИМЕН ──────────────────┐{RESET}')
            print(f'{YELLOW}│ В папке найдено {len(video_files)} видео, но они не переименованы под стандарт!  │{RESET}')
            sample = video_files[0]
            print(f'{YELLOW}│ Пример вашего файла: {sample[:44]:<44} │{RESET}')
            print(f'{YELLOW}│ Требуемый формат:    [Серия]_[Сезон]_[ShikimoriID].расширение     │{RESET}')
            print(f'{YELLOW}│ Например:            [01]_[01]_52991.mp4                          │{RESET}')
            print(f'{YELLOW}└───────────────────────────────────────────────────────────────────┘{RESET}')
            ans_ren = input(f'\n{BOLD}Запустить Smart Rename прямо сейчас для этой папки? [Y/n]: {RESET}').strip().lower()
            if ans_ren not in ('n', 'no', 'н', 'нет'):
                smart_rename()
                continue
            else:
                return
        else:
            print(f'\n{YELLOW}[!] В папке "{videos_dir}" нет подходящих видеофайлов.{RESET}')
            return

    def _sid_sort_key(item):
        sid = str(item.get('shikimori_id', '')).strip()
        try:
            return (0, int(sid))
        except (ValueError, TypeError):
            return (1, sid.lower())

    parsed_items.sort(key=lambda x: (
        _sid_sort_key(x),
        int(x['season_num']),
        int(x['ep_num']),
        x['filename'].lower(),
    ))

    voice_cache = _load_json(CACHE_FILE)
    part_cache = _load_json(PART_CACHE)
    shikimori_titles = {}
    unique_ids = sorted(
        {item['shikimori_id'] for item in parsed_items},
        key=lambda sid: (0, int(str(sid))) if str(sid).isdigit()
        else (1, str(sid).lower())
    )
    print(f'\n{CYAN}Уникальных ID: {len(unique_ids)}{RESET}')
    for sid in unique_ids:
        print(f'\n{MAGENTA}[Shikimori ID {sid}]{RESET} Запрос...')
        title = get_shikimori_title(sid)
        if title is None:
            print(f'  {YELLOW}ID {sid} не найден на Shikimori.{RESET}')
            s_query = input(f'  Поискать по названию (или Enter для ручного ввода): ').strip()
            if s_query:
                results = search_shikimori_animes(s_query, limit=5)
                if results:
                    print(f'  {CYAN}Результаты поиска:{RESET}')
                    for r_i, r_item in enumerate(results, 1):
                        r_t = r_item.get('russian') or r_item.get('name')
                        r_y = (r_item.get('aired_on') or '')[:4]
                        r_e = r_item.get('episodes') or '?'
                        print(f'    {GREEN}{r_i}{RESET}. {BOLD}{r_t}{RESET} ({r_y}, {r_e} эп.)')
                    ans_r = input(f'    Выбор [1-{len(results)}]: ').strip()
                    if ans_r.isdigit() and 1 <= int(ans_r) <= len(results):
                        chosen = results[int(ans_r) - 1]
                        title = chosen.get('russian') or chosen.get('name')
            if not title:
                manual = input(f'  Название вручную для {sid}: ').strip()
                title = manual if manual else f'Anime_{sid}'
        shikimori_titles[sid] = title
        print(f'  {GREEN}Название: {title}{RESET}')

        if sid not in voice_cache or not voice_cache[sid]:
            # Пробуем подтянуть название озвучки из метаданных первой серии этого ID
            detected_voice = ''
            first_for_sid = next((it['filepath'] for it in parsed_items if it['shikimori_id'] == sid), None)
            if first_for_sid and use_ffmpeg:
                try:
                    p_info = _probe_all(first_for_sid, ffprobe, ffmpeg)
                    auds = p_info.get('audio') or []
                    if auds and auds[0].get('title'):
                        detected_voice = auds[0]['title']
                except Exception:
                    pass
            prompt_v = f'  Озвучка [{detected_voice}]: ' if detected_voice else '  Озвучка: '
            voice = input(prompt_v).strip() or detected_voice
            voice_cache[sid] = voice if voice else 'Не указана'
            _save_json(CACHE_FILE, voice_cache)
        else:
            print(f'  {DIM}Озвучка (кэш): {voice_cache[sid]}{RESET}')

        if sid not in part_cache:
            part = input('  Часть (Enter — нет): ').strip()
            part_cache[sid] = part
            _save_json(PART_CACHE, part_cache)
        else:
            print(f'  {DIM}Часть (кэш): {part_cache[sid] or "нет"}{RESET}')

    print(f'\n{CYAN}{BOLD}═══ СПИСОК ФАЙЛОВ (СТРОГО ПО ID) ═══{RESET}')
    queue = []
    current_sid = None
    for item in parsed_items:
        sid = item['shikimori_id']
        if sid != current_sid:
            current_sid = sid
            print(f'\n{MAGENTA}{BOLD}>>> ID {sid}: начало группы <<< {RESET}')
        title = shikimori_titles[sid]
        hashtag = make_hashtag(title)
        voice = voice_cache[sid]
        part = part_cache.get(sid, '').strip()
        part_str = f' Часть {part}' if part else ''
        caption = (f'{hashtag}\nОзвучка: {voice}\n'
                   f'Сезон {item["season_num"]}{part_str} Серия {item["ep_num"]}')
        queue.append({
            'filepath': item['filepath'], 'filename': item['filename'],
            'caption': caption, 'sid': sid, 'title': title,
            'season': item['season_num'], 'ep': item['ep_num'],
        })
        print(f'\n{BOLD}{item["filename"]}{RESET}')
        print(f'{DIM}{caption}{RESET}')
        print(f'{CYAN}{"─"*50}{RESET}')

    while True:
        print(f'\n{CYAN}┌─────────────────────── КОМАНДЫ ПЕРЕД ЗАГРУЗКОЙ ───────────────────────┐{RESET}')
        print(f'  {GREEN}1{RESET}. {BOLD}Начать загрузку в Telegram{RESET}   {DIM}(или напишите start){RESET}')
        print(f'  {GREEN}2{RESET}. Изменить озвучку           {DIM}(или edit voice <id>){RESET}')
        print(f'  {GREEN}3{RESET}. Изменить название тайтла   {DIM}(или edit title <id>){RESET}')
        print(f'  {GREEN}4{RESET}. Изменить часть сезона      {DIM}(или edit part <id>){RESET}')
        print(f'  {GREEN}5{RESET}. Показать все посты         {DIM}(или show){RESET}')
        print(f'  {RED}0{RESET}. Отмена                     {DIM}(или cancel){RESET}')
        print(f'{CYAN}└───────────────────────────────────────────────────────────────────────┘{RESET}')
        cmd = input(f'{BOLD}Действие [1-5, 0 = отмена, Enter = 1]: {RESET}').strip().lower()
        if not cmd or cmd in ('1', 'start'):
            break
        if cmd in ('0', 'cancel'):
            print(f'{YELLOW}Загрузка отменена.{RESET}')
            return
        if cmd in ('5', 'show'):
            for it in queue:
                print(f'\n{BOLD}{it["filename"]}{RESET} (ID {it["sid"]}):\n{it["caption"]}')
            continue

        unique_sids = list(shikimori_titles.keys())

        if cmd in ('2', 'voice') or cmd.startswith('edit voice'):
            target_sid = None
            if cmd.startswith('edit voice '):
                target_sid = cmd.split('edit voice ', 1)[1].strip()
            elif len(unique_sids) == 1:
                target_sid = unique_sids[0]
            else:
                print(f'\n{BOLD}Выберите тайтл для смены озвучки:{RESET}')
                for s_i, sid_k in enumerate(unique_sids, 1):
                    print(f'  {GREEN}{s_i}{RESET}. {shikimori_titles[sid_k]} (ID {sid_k}) — сейчас: {voice_cache.get(sid_k, "Не указана")}')
                c_idx = input(f'{BOLD}Номер тайтла [1-{len(unique_sids)}]: {RESET}').strip()
                if c_idx.isdigit() and 1 <= int(c_idx) <= len(unique_sids):
                    target_sid = unique_sids[int(c_idx) - 1]

            if target_sid and target_sid in shikimori_titles:
                nv = input(f'  Новая озвучка для "{shikimori_titles[target_sid]}" (ID {target_sid}): ').strip()
                if nv:
                    voice_cache[target_sid] = nv
                    _save_json(CACHE_FILE, voice_cache)
                    for it in queue:
                        if it['sid'] == target_sid:
                            p = f' Часть {part_cache.get(target_sid, "")}' if part_cache.get(target_sid, '').strip() else ''
                            it['caption'] = (f'{make_hashtag(shikimori_titles[target_sid])}\n'
                                             f'Озвучка: {nv}\n'
                                             f'Сезон {it["season"]}{p} Серия {it["ep"]}')
                    print(f'{GREEN}✓ Озвучка обновлена на "{nv}".{RESET}')
            continue

        if cmd in ('3', 'title') or cmd.startswith('edit title'):
            target_sid = None
            if cmd.startswith('edit title '):
                target_sid = cmd.split('edit title ', 1)[1].strip()
            elif len(unique_sids) == 1:
                target_sid = unique_sids[0]
            else:
                print(f'\n{BOLD}Выберите тайтл для смены названия:{RESET}')
                for s_i, sid_k in enumerate(unique_sids, 1):
                    print(f'  {GREEN}{s_i}{RESET}. {shikimori_titles[sid_k]} (ID {sid_k})')
                c_idx = input(f'{BOLD}Номер тайтла [1-{len(unique_sids)}]: {RESET}').strip()
                if c_idx.isdigit() and 1 <= int(c_idx) <= len(unique_sids):
                    target_sid = unique_sids[int(c_idx) - 1]

            if target_sid and target_sid in shikimori_titles:
                nt = input(f'  Новое название для ID {target_sid} (текущее: "{shikimori_titles[target_sid]}"): ').strip()
                if nt:
                    shikimori_titles[target_sid] = nt
                    for it in queue:
                        if it['sid'] == target_sid:
                            p = f' Часть {part_cache.get(target_sid, "")}' if part_cache.get(target_sid, '').strip() else ''
                            it['caption'] = (f'{make_hashtag(nt)}\n'
                                             f'Озвучка: {voice_cache.get(target_sid, "Не указана")}\n'
                                             f'Сезон {it["season"]}{p} Серия {it["ep"]}')
                    print(f'{GREEN}✓ Название обновлено на "{nt}".{RESET}')
            continue

        if cmd in ('4', 'part') or cmd.startswith('edit part'):
            target_sid = None
            if cmd.startswith('edit part '):
                target_sid = cmd.split('edit part ', 1)[1].strip()
            elif len(unique_sids) == 1:
                target_sid = unique_sids[0]
            else:
                print(f'\n{BOLD}Выберите тайтл для смены части сезона:{RESET}')
                for s_i, sid_k in enumerate(unique_sids, 1):
                    cur_p = part_cache.get(sid_k, "нет")
                    print(f'  {GREEN}{s_i}{RESET}. {shikimori_titles[sid_k]} (ID {sid_k}) — текущая часть: {cur_p}')
                c_idx = input(f'{BOLD}Номер тайтла [1-{len(unique_sids)}]: {RESET}').strip()
                if c_idx.isdigit() and 1 <= int(c_idx) <= len(unique_sids):
                    target_sid = unique_sids[int(c_idx) - 1]

            if target_sid and target_sid in shikimori_titles:
                np_ = input(f'  Новая часть для {target_sid} (например 1 или 2, Enter — убрать): ').strip()
                part_cache[target_sid] = np_
                _save_json(PART_CACHE, part_cache)
                for it in queue:
                    if it['sid'] == target_sid:
                        p = f' Часть {np_}' if np_ else ''
                        it['caption'] = (f'{make_hashtag(shikimori_titles[target_sid])}\n'
                                         f'Озвучка: {voice_cache.get(target_sid, "Не указана")}\n'
                                         f'Сезон {it["season"]}{p} Серия {it["ep"]}')
                print(f'{GREEN}✓ Часть сезона обновлена.{RESET}')
            continue

        print(f'{RED}  Неизвестная команда. Введите цифру 1-5 или 0.{RESET}')

    confirm = input(f'\n{BOLD}Начать залив {len(queue)} видео? (y/n): {RESET}').strip().lower()
    if confirm != 'y':
        print(f'{YELLOW}Отмена.{RESET}')
        return

    total_q = len(queue)

    prep_worker = None
    first_prep = None
    if use_ffmpeg and queue:
        try:
            prep_worker = _PrepWorkerClient(ffmpeg, ffprobe, thumb_dir)
            print(f'\n{CYAN}{BOLD}[*] Подготовка первого файла...{RESET}')
            first_job = prep_worker.request(queue[0])
            first_prep = await prep_worker.wait(first_job)
            print(f'{GREEN}{BOLD}✓ Первый файл подготовлен.{RESET}')
        except Exception as e:
            print(f'{YELLOW}[!] Подготовка первого файла завершилась с ошибкой: {e}{RESET}')
            if prep_worker is not None:
                prep_worker.close()
                prep_worker = None
            first_prep = {
                'video_path': queue[0]['filepath'], 'thumb_path': None,
                'width': None, 'height': None, 'duration': None,
            }
    else:
        first_prep = {
            'video_path': queue[0]['filepath'], 'thumb_path': None,
            'width': None, 'height': None, 'duration': None,
        }

    cfg = load_config()
    cfg_api_id = int(cfg.get('api_id') or API_ID)
    cfg_api_hash = str(cfg.get('api_hash') or API_HASH)

    os.makedirs(TDLIB_SESSION_DIR, exist_ok=True)
    print(f'\n{CYAN}[*] Подготовка TDJSON клиента...{RESET}')
    print(f'    session:  {TDLIB_SESSION_DIR}')
    print(f'    api_id:   {cfg_api_id}')

    td = _TDJsonDirect(TDLIB_SESSION_DIR)
    await td.start()

    try:
        td.tdjson.td_send(
            td.client_id,
            json.dumps({'@type': 'getOption', 'name': 'version'}).encode('utf-8')
        )
        await _authorize_tdjson(td, cfg_api_id, cfg_api_hash)
    except Exception as e:
        td.stop()
        print(f'{RED}[!] Ошибка запуска/авторизации TDJSON: {type(e).__name__}: {e}{RESET}')
        if prep_worker is not None:
            prep_worker.close()
        return

    chat_id = None
    chat_title = 'N/A'

    def _raw_err(res):
        if isinstance(res, dict) and res.get('@type') == 'error':
            return f"{res.get('code')}: {res.get('message')}"
        return None

    async def _resolve_invite(invite_link):
        nonlocal chat_id, chat_title
        inv = (invite_link or '').strip()
        if not inv:
            return False

        res = await td.request(
            {'@type': 'joinChatByInviteLink', 'invite_link': inv},
            timeout=120,
        )
        err = _raw_err(res)

        if not err:
            chat_id = int(res.get('id') or 0)
            chat_title = res.get('title') or str(chat_id)
            return bool(chat_id)

        if 'USER_ALREADY_PARTICIPANT' in err:
            info = await td.request(
                {'@type': 'checkChatInviteLink', 'invite_link': inv},
                timeout=120,
            )
            info_err = _raw_err(info)
            if info_err:
                print(f'{YELLOW}[!] checkChatInviteLink: {info_err}{RESET}')
                return False

            cid = int(info.get('chat_id') or 0)
            if cid:
                chat_id = cid
                chat_title = info.get('title') or str(cid)
                print(f'{GREEN}✓ Уже состоим в чате, найден ID через invite: {chat_id}{RESET}')
                return True
            return False

        print(f'{YELLOW}[!] joinChatByInviteLink: {err}{RESET}')
        return False

    if isinstance(channel_input_str, str) and not channel_input_str.lstrip('-').isdigit():
        s = channel_input_str.strip()
        if s.startswith('http'):
            await _resolve_invite(s)
        else:
            uname = s.lstrip('@')
            res = await td.request({'@type': 'searchPublicChat', 'username': uname}, timeout=120)
            err = _raw_err(res)
            if err:
                print(f'{YELLOW}[!] searchPublicChat: {err}{RESET}')
            else:
                chat_id = res.get('id')
                chat_title = res.get('title') or str(chat_id)

    if chat_id is None:
        raw = str(channel_input_str).strip()
        if raw.lstrip('-').isdigit():
            cid = int(raw)
            res = await td.request({'@type': 'getChat', 'chat_id': cid}, timeout=120)
            err = _raw_err(res)
            if err:
                print(f'{YELLOW}[!] getChat: {err}{RESET}')
            else:
                chat_id = res.get('id')
                chat_title = res.get('title') or str(chat_id)

    if chat_id is None:
        print(f'{YELLOW}Не удалось найти чат. Введи инвайт-ссылку.{RESET}')
        inv = input(f'{BOLD}Инвайт-ссылка [{cfg.get("invite_link", INVITE_LINK)}]: {RESET}').strip()
        inv = inv or cfg.get('invite_link', INVITE_LINK)
        await _resolve_invite(inv)

    if chat_id is None:
        print(f'{RED}Не удалось определить чат. Выход.{RESET}')
        if prep_worker is not None:
            prep_worker.close()
        td.stop()
        return

    print(f'{GREEN}✓ Чат: {chat_title} (ID: {chat_id}){RESET}')
    cfg['channel'] = str(chat_id)
    save_config(cfg)

    print(f'\n{CYAN}{BOLD}[*] ФАЗА 3: залив {total_q} файлов...{RESET}')

    failed = []
    next_prep_job = None

    try:
        active_sid = None
        for idx, item in enumerate(queue, 1):
            i = idx - 1

            if item['sid'] != active_sid:
                active_sid = item['sid']
                print(f'\n{MAGENTA}{BOLD}════════ ID {active_sid} ════════{RESET}')

            if idx == 1:
                prep = first_prep
            elif next_prep_job is not None and prep_worker is not None:
                try:
                    prep = await prep_worker.wait(next_prep_job)
                except Exception as e:
                    print(f'\n  {YELLOW}[!] Ошибка подготовки следующего файла: {e}{RESET}')
                    prep = None
            else:
                prep = None

            if prep is None:
                prep = {
                    'video_path': item['filepath'],
                    'thumb_path': None,
                    'width': None, 'height': None, 'duration': None,
                }

            next_prep_job = None
            if i + 1 < total_q and use_ffmpeg and prep_worker is not None:
                next_item = queue[i + 1]
                try:
                    next_prep_job = prep_worker.request(next_item)
                    print(f'\n  {DIM}↳ Подготовка следующего: {next_item["filename"]}{RESET}')
                except Exception as e:
                    print(f'\n  {YELLOW}[!] Не удалось запустить подготовку следующего: {e}{RESET}')

            print(f'\n{CYAN}{BOLD}[{idx}/{total_q}]{RESET} {item["filename"]}')

            video_to_upload = prep['video_path']
            thumb_to_upload = prep['thumb_path']
            width = prep['width']
            height = prep['height']
            duration = prep['duration']

            try:
                size_mb = os.path.getsize(video_to_upload) / (1024 * 1024)
                print(f'  {DIM}размер: {size_mb:.0f} МБ{RESET}')
            except OSError:
                pass
            if width and height and duration:
                print(f'  {width}x{height}, {duration:.1f}с')

            try:
                file_size = os.path.getsize(video_to_upload)
            except OSError:
                file_size = 0

            content = {
                '@type': 'inputMessageVideo',
                'video': {
                    '@type': 'inputFileLocal',
                    'path': os.path.abspath(video_to_upload),
                },
                'duration': int(duration or 0),
                'width': int(width or 0),
                'height': int(height or 0),
                'caption': {
                    '@type': 'formattedText',
                    'text': item['caption'],
                },
                'show_caption_above_media': False,
                'supports_streaming': True,
            }
            if thumb_to_upload and os.path.exists(thumb_to_upload):
                content['thumbnail'] = {
                    '@type': 'inputThumbnail',
                    'thumbnail': {
                        '@type': 'inputFileLocal',
                        'path': os.path.abspath(thumb_to_upload),
                    },
                    'width': 320,
                    'height': 180,
                }

            progress_state = _ProgressState(file_size)
            stop_spin = threading.Event()
            spin_th = threading.Thread(
                target=_spin_tdlib_progress,
                args=(progress_state, item['filename'], stop_spin),
                daemon=True,
            )
            spin_th.start()

            try:
                await td.send_video_and_wait(
                    chat_id=chat_id,
                    content=content,
                    progress_state=progress_state,
                )
                progress_state.update(file_size)
                print(f'\n  {GREEN}✓ Telegram подтвердил отправку.{RESET}')
            except Exception as e:
                failed.append(item['filename'])
                msg = str(e)
                print(f'\n  {RED}[!] Файл НЕ подтверждён Telegram: {type(e).__name__}: {e}{RESET}')
                if 'InputFile is not specified' in msg:
                    print(f'\n{RED}{BOLD}[!] Остановка: TDLib отклонила InputFile.{RESET}')
                    raise SystemExit(2)
            finally:
                stop_spin.set()
                spin_th.join(timeout=0.6)

        print(f'\n{CYAN}{BOLD}═══ ИТОГ ═══{RESET}')
        if not failed:
            print(f'{GREEN}✓ Все файлы подтверждены Telegram.{RESET}')
            send_telegram_notification(f"✅ <b>Telegram Upload завершен!</b>\nУспешно отправлено {len(queue)} видео.")
        else:
            print(f'{RED}✗ Не подтверждены:{RESET}')
            for name in failed:
                print(f'   {RED}• {name}{RESET}')
            send_telegram_notification(f"⚠️ <b>Telegram Upload завершен с ошибками</b>\nУспешно: {len(queue) - len(failed)}/{len(queue)}.\nНе отправлены: {', '.join(failed)}")

    finally:
        if next_prep_job is not None and prep_worker is not None:
            try:
                await prep_worker.wait(next_prep_job, timeout=5)
            except Exception:
                pass
        if prep_worker is not None:
            try:
                prep_worker.close()
            except Exception:
                pass
        td.stop()


def telegram_uploader():
    try:
        asyncio.run(_tdlib_uploader_async())
    except KeyboardInterrupt:
        print(f'\n{YELLOW}Прервано.{RESET}')
    except Exception as e:
        import traceback
        print(f'\n{RED}{BOLD}═══ СКРИПТ УПАЛ ═══{RESET}')
        print(f'{RED}{type(e).__name__}: {e}{RESET}\n')
        traceback.print_exc()


# ============================================================================
#  БЛОК 2 — REMUX В MP4 (С ВЫБОРОМ И ВШИВКОЙ ОЗВУЧЕК)
# ============================================================================
_remux_state = {}
_remux_lock = threading.Lock()
_remux_lines_drawn = 0


def _remux_render():
    global _remux_lines_drawn
    with _remux_lock:
        items = sorted(_remux_state.items())
    if not items:
        return
    buf = []
    for slot, st in items:
        name = st['name']
        if len(name) > 45:
            name = name[:42] + '...'
        status = st.get('status', 'run')
        pct = st.get('percent', 0.0)
        if status == 'ok':
            bar = _make_bar(100.0, 20)
            buf.append(f'  {DIM}{name:<45}{RESET} {GREEN}{bar}{RESET} {GREEN}OK{RESET}')
        elif status == 'fail':
            bar = _make_bar(pct, 20)
            buf.append(f'  {DIM}{name:<45}{RESET} {RED}{bar}{RESET} {RED}FAIL{RESET}')
        elif pct < 0:
            buf.append(f'  {name:<45} {CYAN}··· ··· ··· ··· ···{RESET} {CYAN}running{RESET}')
        else:
            bar = _make_bar(pct, 20)
            buf.append(f'  {name:<45} {CYAN}{bar}{RESET} {pct:5.1f}%')
    if _remux_lines_drawn > 0:
        sys.stdout.write(f'\033[{_remux_lines_drawn}A')
    for line in buf:
        sys.stdout.write('\033[2K' + line + '\n')
    sys.stdout.flush()
    _remux_lines_drawn = len(buf)


def _remux_display_loop(stop_event):
    while not stop_event.wait(0.25):
        _remux_render()


def _consume_progress(proc, duration, slot):
    try:
        for raw in proc.stdout:
            if not raw:
                continue
            if raw.startswith('out_time_ms=') and duration > 0:
                try:
                    sec = int(raw.split('=', 1)[1]) / 1_000_000
                    pct = min(99.5, sec / duration * 100.0)
                    with _remux_lock:
                        if slot in _remux_state:
                            _remux_state[slot]['percent'] = pct
                except (ValueError, IndexError):
                    pass
            elif raw.startswith('progress=end'):
                with _remux_lock:
                    if slot in _remux_state:
                        _remux_state[slot]['percent'] = 99.5
    except Exception:
        pass


def _run_remux_batch(files, workers, worker_fn):
    global _remux_lines_drawn
    with _remux_lock:
        _remux_state.clear()
        for i, f in enumerate(files):
            _remux_state[i] = {'name': f, 'percent': 0.0, 'status': 'run'}
    _remux_lines_drawn = 0

    stop_event = threading.Event()
    disp = threading.Thread(target=_remux_display_loop,
                            args=(stop_event,), daemon=True)
    disp.start()

    results = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(worker_fn, f, i): (i, f)
                    for i, f in enumerate(files)}
            for fut in as_completed(futs):
                slot, f = futs[fut]
                try:
                    src, ok, err = fut.result()
                    results.append((src, ok))
                    if err:
                        print(f'\n  {YELLOW}[!]{RESET} {src}: {err}')
                except Exception as e:
                    with _remux_lock:
                        if slot in _remux_state:
                            _remux_state[slot]['status'] = 'fail'
                    print(f'\n  {RED}[ERR]{RESET} {f}: {e}')
                    results.append((f, False))
    finally:
        stop_event.set()
        disp.join(timeout=1.0)
        _remux_render()
        print()
        _remux_lines_drawn = 0

    failed = [f for f, ok in results if not ok]
    if failed:
        print(f'{YELLOW}Неудачные:{RESET}')
        for f in failed:
            print(f'  {RED}•{RESET} {f}')
    ok = sum(1 for _, s in results if s)
    print(f'{GREEN}{BOLD}Готово: {ok}/{len(results)}{RESET}')


def _remux_direct_worker(f, slot, ffmpeg, ffprobe, audio_plan=None, del_external=False):
    """Быстрый путь: видео + выбранная/внешняя озвучка + субтитры → MP4 через ffmpeg."""
    name, _ = os.path.splitext(f)
    tmp_out = f'{name}.remux_direct.tmp.mp4'
    final   = f'{name}.mp4'

    try:
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
    except OSError:
        pass

    try:
        info = _probe_all(f, ffprobe, ffmpeg)
        duration = info.get('duration') or 0
    except Exception:
        info = {'duration': 0, 'audio': []}
        duration = 0

    inputs, maps, a_args, _ = _build_ffmpeg_audio_mapping(
        f, info, audio_plan, ffprobe, ffmpeg, aac_bitrate='192k'
    )

    voice_title = (audio_plan or {}).get('voice_title') or ''
    meta_args = ['-metadata:s:a:0', f'title={voice_title}'] if voice_title else []

    # Попытка 1: с сохраненными субтитрами (-c:s mov_text)
    maps_with_sub = maps + ['-map', '0:s?']
    cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
           '-progress', 'pipe:1', '-nostats'] + inputs + maps_with_sub + \
          ['-c:v', 'copy', '-c:s', 'mov_text', '-dn', '-threads', '0'] + a_args + meta_args + \
          ['-movflags', '+faststart', tmp_out]

    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1, encoding='utf-8',
                            errors='ignore')
    _consume_progress(proc, duration, slot)
    try:
        _, stderr_data = proc.communicate(timeout=3600)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
        except OSError:
            pass
        with _remux_lock:
            if slot in _remux_state:
                _remux_state[slot]['status'] = 'fail'
        return f, False, 'ffmpeg timeout'

    # Если с субтитрами не удалось (например, PGS), опускаем их (-sn)
    if proc.returncode != 0 or not os.path.exists(tmp_out):
        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
        except OSError:
            pass

        cmd_no_sub = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
                      '-progress', 'pipe:1', '-nostats'] + inputs + maps + \
                     ['-c:v', 'copy', '-sn', '-dn', '-threads', '0'] + a_args + meta_args + \
                     ['-movflags', '+faststart', tmp_out]

        proc = subprocess.Popen(cmd_no_sub, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1, encoding='utf-8',
                                errors='ignore')
        _consume_progress(proc, duration, slot)
        try:
            _, stderr_data = proc.communicate(timeout=3600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            try:
                if os.path.exists(tmp_out):
                    os.remove(tmp_out)
            except OSError:
                pass
            with _remux_lock:
                if slot in _remux_state:
                    _remux_state[slot]['status'] = 'fail'
            return f, False, 'ffmpeg timeout'

    if proc.returncode != 0 or not os.path.exists(tmp_out):
        err = (stderr_data or '')[-400:].replace('\n', ' | ')
        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
        except OSError:
            pass
        with _remux_lock:
            if slot in _remux_state:
                _remux_state[slot]['status'] = 'fail'
        return f, False, f'ffmpeg rc={proc.returncode}: {err}'

    try:
        os.replace(tmp_out, final)
    except OSError as e:
        with _remux_lock:
            if slot in _remux_state:
                _remux_state[slot]['status'] = 'fail'
        return f, False, f'replace: {e}'

    if del_external and audio_plan:
        ext_f = (audio_plan.get('external_map') or {}).get(f)
        if ext_f and os.path.exists(ext_f):
            try:
                os.remove(ext_f)
            except OSError:
                pass

    if os.path.abspath(f) != os.path.abspath(final):
        deleted = False
        for _ in range(10):
            try:
                os.remove(f)
                deleted = True
                break
            except OSError:
                time.sleep(1)
        if not deleted:
            with _remux_lock:
                if slot in _remux_state:
                    _remux_state[slot]['status'] = 'ok'
                    _remux_state[slot]['percent'] = 100.0
            return f, True, f'исходник занят (результат: {final})'

    with _remux_lock:
        if slot in _remux_state:
            _remux_state[slot]['status'] = 'ok'
            _remux_state[slot]['percent'] = 100.0
    return f, True, None


def remux_mp4():
    print(f'\n{CYAN}{BOLD}═══ Remux в MP4 (быстрая переупаковка без потери качества) ═══{RESET}')
    print(f'{DIM}• Видеопоток не пережимается (-c copy) — сохраняется 100% исходное качество.{RESET}')
    print(f'{DIM}• Добавляется faststart для мгновенного стриминга в плеере Telegram.{RESET}')

    ffmpeg  = get_tool_path('ffmpeg.exe'  if os.name == 'nt' else 'ffmpeg')
    ffprobe = get_tool_path('ffprobe.exe' if os.name == 'nt' else 'ffprobe')

    if not _have(ffmpeg):
        print(f'{RED}[!] Нужен ffmpeg!{RESET}')
        return
    if not _have(ffprobe):
        ffprobe = None

    exts = ('*.mkv', '*.avi', '*.mov', '*.m4v', '*.flv', '*.webm', '*.ts',
            '*.hevc', '*.h264', '*.m2ts', '*.ogm', '*.mpg', '*.mp4')
    files = []
    for e in exts:
        files.extend(glob.glob(e))
    files = sorted(set(files), key=natural_sort_key)
    if not files:
        print(f'{YELLOW}Видео не найдено.{RESET}')
        return

    print(f'\n{CYAN}Найдено: {len(files)} видеофайл(ов){RESET}')
    for f in files[:10]:
        print(f'  {DIM}• {f}{RESET}')
    if len(files) > 10:
        print(f'  {DIM}... и ещё {len(files) - 10}{RESET}')

    audio_plan = _prompt_audio_plan(files, ffprobe, ffmpeg)
    del_ext = False
    if audio_plan['mode'] in ('external_replace', 'external_first'):
        ans_del = input(f'{BOLD}Удалить внешние файлы озвучки после успешной вшивки? [y/N]: {RESET}').strip().lower()
        del_ext = ans_del in ('y', 'yes', 'д', 'да')

    confirm = input(f'\n{BOLD}Запустить ремуксинг {len(files)} файл(ов) в MP4? (y/n): {RESET}').strip().lower()
    if confirm != 'y':
        print(f'{YELLOW}Отмена.{RESET}')
        return

    def _worker(f, slot):
        return _remux_direct_worker(f, slot, ffmpeg, ffprobe, audio_plan, del_ext)

    _run_remux_batch(files, WORKERS_REMUX, _worker)
    send_telegram_notification(f"✅ <b>Remux в MP4 завершен!</b>\nОбработано {len(files)} файлов.")


# ============================================================================
#  БЛОК 3 — SMART RENAME
# ============================================================================
def smart_rename():
    print(f'\n{CYAN}{BOLD}═══ Smart Rename — Умное переименование серий ═══{RESET}')
    print(f'{DIM}Приводит имена файлов к единому стандарту тулкита: [Серия]_[Сезон]_[ShikimoriID].расширение{RESET}')
    print(f'{DIM}Пример: [01]_[01]_52991.mp4 (Серия 01, Сезон 01, ID Shikimori 52991){RESET}')

    exts = ('*.mp4', '*.mkv', '*.avi', '*.mov', '*.m4v', '*.mka', '*.aac', '*.ac3', '*.mp3', '*.flac')
    files = []
    for e in exts:
        files.extend(glob.glob(e))
    files = sorted(set(files), key=natural_sort_key)
    if not files:
        print(f'{YELLOW}В текущей папке нет подходящих файлов.{RESET}')
        return

    print(f'\n{CYAN}─── Параметры сезона и тайтла ───{RESET}')
    print(f'{DIM}Номер сезона будет записан в формате [01], [02] и т.д.{RESET}')
    season_input = input(f'{BOLD}Номер сезона [Enter = 01]: {RESET}').strip() or '01'
    if not season_input.isdigit():
        print(f'{YELLOW}Введено не число — беру "01".{RESET}')
        season_input = '01'
    else:
        season_input = season_input.zfill(2)

    print(f'\n{DIM}Введите ID с сайта Shikimori (например "52991") или название аниме (русское / английское).{RESET}')
    print(f'{DIM}Если ввести название, тулкит автоматически найдет сериал через API Shikimori.{RESET}')
    prefix = input(f'{BOLD}Название или Shikimori ID (Enter — без ID): {RESET}').strip()
    if prefix and not prefix.isdigit():
        print(f'\n{CYAN}Поиск "{prefix}" на Shikimori...{RESET}')
        results = search_shikimori_animes(prefix, limit=5)
        if results:
            print(f'{CYAN}Найдено на Shikimori:{RESET}')
            for r_i, r_item in enumerate(results, 1):
                r_title = r_item.get('russian') or r_item.get('name')
                r_year = (r_item.get('aired_on') or '')[:4]
                r_eps = r_item.get('episodes') or '?'
                print(f'  {GREEN}{r_i}{RESET}. {BOLD}{r_title}{RESET} (ID: {r_item["id"]}, {r_eps} эп., {r_year})')
            print(f'  {YELLOW}0{RESET}. Оставить "{prefix}" текстом без ID')
            ans_s = input(f'{BOLD}Выберите тайтл [0-{len(results)}, Enter = 1]: {RESET}').strip() or '1'
            if ans_s.isdigit() and 1 <= int(ans_s) <= len(results):
                chosen = results[int(ans_s) - 1]
                prefix = str(chosen['id'])
                print(f'  {GREEN}✓ Установлен Shikimori ID: {prefix} ({chosen.get("russian") or chosen.get("name")}){RESET}')

    ignore_set = {'10-bit', '2160', '720', '265', '8-bit', '480', '264', '1080'}
    bit_re = re.compile(r'\b(10-?bit|8-?bit)\b', re.IGNORECASE)
    num_re = re.compile(r'\d+')
    files_with_nums = []
    for filename in files:
        name, ext = os.path.splitext(filename)
        name_clean = bit_re.sub('', name)
        numbers = num_re.findall(name_clean)
        valid = [n for n in numbers if n not in ignore_set and int(n) < 1080]
        if valid:
            files_with_nums.append((filename, valid, ext))
    if not files_with_nums:
        print(f'{YELLOW}[!] В названиях файлов не обнаружены номера серий.{RESET}')
        return

    first_file, first_nums, _ = files_with_nums[0]
    print(f'\n{CYAN}─── Определение номера серии ───{RESET}')
    print(f'Анализ первого файла: {BOLD}{first_file}{RESET}')
    print(f'Найденные в названии числа:')
    for i, num in enumerate(first_nums):
        hint = f"{GREEN}← вероятнее всего серия{RESET}" if i == 0 else ""
        print(f'  {GREEN}{i+1}{RESET}. Число {BOLD}{num}{RESET} {hint}')

    try:
        raw_choice = input(f'\n{BOLD}Какая позиция обозначает номер серии? [1-{len(first_nums)}, Enter = 1]: {RESET}').strip() or '1'
        choice = int(raw_choice)
    except ValueError:
        print(f'{RED}Неверный ввод.{RESET}')
        return
    if not (1 <= choice <= len(first_nums)):
        print(f'{RED}Неверная позиция.{RESET}')
        return
    idx = choice - 1

    rename_plan = []
    for filename, nums, ext in files_with_nums:
        ep_num = (nums[idx] if idx < len(nums) else nums[-1]).zfill(2)
        if prefix and season_input:
            new_name = f'[{ep_num}]_[{season_input}]_{prefix}{ext}'
        elif prefix and not season_input:
            new_name = f'[{ep_num}]_{prefix}{ext}'
        elif not prefix and season_input:
            new_name = f'[{ep_num}]_[{season_input}]{ext}'
        else:
            new_name = f'[{ep_num}]{ext}'
        if filename != new_name:
            rename_plan.append((filename, new_name))

    if not rename_plan:
        print(f'\n{GREEN}✓ Все файлы ({len(files)} шт.) уже названы корректно по стандарту.{RESET}')
        return

    print(f'\n{CYAN}─── Предпросмотр переименования ({len(rename_plan)} файлов) ───{RESET}')
    for old_name, new_name in rename_plan[:10]:
        print(f'  {DIM}{old_name}{RESET}  ──>  {GREEN}{BOLD}{new_name}{RESET}')
    if len(rename_plan) > 10:
        print(f'  {DIM}... и ещё {len(rename_plan) - 10} файлов{RESET}')

    ans = input(f'\n{BOLD}Применить переименование к {len(rename_plan)} файлам? [Y/n]: {RESET}').strip().lower()
    if ans in ('n', 'no', 'н', 'нет', '0'):
        print(f'{YELLOW}Переименование отменено.{RESET}')
        return

    ok_ren = 0
    for old_name, new_name in rename_plan:
        counter = 1
        final_new_name = new_name
        while os.path.exists(final_new_name) and old_name.lower() != final_new_name.lower():
            n, e = os.path.splitext(new_name)
            final_new_name = f'{n}_{counter}{e}'
            counter += 1
        try:
            os.rename(old_name, final_new_name)
            ok_ren += 1
        except Exception as e:
            print(f'{RED}Не удалось переименовать {old_name}: {e}{RESET}')
    print(f'\n{GREEN}{BOLD}✓ Готово! Успешно переименовано {ok_ren} из {len(rename_plan)} файлов.{RESET}')


# ============================================================================
#  БЛОК 4 — BDREMUX COMPRESS (С ВЫБОРОМ ОЗВУЧКИ)
# ============================================================================
def _run_ffmpeg_progress(cmd, duration, label):
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, bufsize=1)
    start = time.time()
    last = {'pct': 0.0, 'fps': 0.0, 'speed': '', 'bitrate': ''}
    last_draw = 0.0

    try:
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw or '=' not in raw:
                continue
            k, v = raw.split('=', 1)
            if k == 'out_time_ms':
                try:
                    sec = int(v) / 1_000_000
                    if duration > 0:
                        last['pct'] = min(100.0, sec / duration * 100.0)
                except ValueError:
                    pass
            elif k == 'fps':
                try:
                    last['fps'] = float(v)
                except ValueError:
                    pass
            elif k == 'speed':
                last['speed'] = v.strip()
            elif k == 'bitrate':
                last['bitrate'] = v.strip()
            elif k == 'progress' and v.strip() == 'end':
                last['pct'] = 100.0

            now = time.time()
            if now - last_draw < 0.2 and last['pct'] < 100.0:
                continue
            last_draw = now

            elapsed = now - start
            eta = ''
            if last['pct'] > 0.5 and duration > 0:
                total_est = elapsed / (last['pct'] / 100.0)
                remain = max(0.0, total_est - elapsed)
                eta = f'  ETA {_fmt_time(remain)}'
            bar = _make_bar(last['pct'], 24)
            fps_s = f'{last["fps"]:5.0f} fps' if last['fps'] else '        '
            spd = f'{last["speed"]:>7}' if last['speed'] else '       '
            br = f'{last["bitrate"]:>10}' if last['bitrate'] else '          '
            sys.stdout.write(
                f'\r  {CYAN}{bar}{RESET} {last["pct"]:5.1f}%  '
                f'{fps_s}  {spd}  {br}{eta}   ')
            sys.stdout.flush()
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        proc.wait()
        sys.stdout.write('\n')
        sys.stdout.flush()
        raise
    sys.stdout.write('\n')
    sys.stdout.flush()
    return proc.returncode


@lru_cache(maxsize=1)
def detect_best_hevc_encoder(ffmpeg):
    """
    Автоматически определяет наилучший доступный HEVC энкодер:
    NVIDIA NVENC -> Intel QSV -> AMD AMF -> CPU libx265
    """
    candidates = [
        ('hevc_nvenc', ['-hwaccel', 'cuda']),
        ('hevc_qsv',   ['-hwaccel', 'qsv']),
        ('hevc_amf',   []),
        ('libx265',    []),
    ]
    for enc, hw_args in candidates:
        test_cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error'] + hw_args + [
            '-f', 'lavfi', '-i', 'color=s=64x64:d=0.1',
            '-c:v', enc, '-f', 'null', '-'
        ]
        try:
            res = subprocess.run(test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
            if res.returncode == 0:
                return enc, hw_args
        except Exception:
            continue
    return 'libx265', []


def _build_encoder_args(enc, cq, avg_kbps, maxrate_kbps, bufsize):
    if enc == 'hevc_nvenc':
        return [
            '-c:v', 'hevc_nvenc', '-preset', 'p4', '-rc', 'vbr',
            '-cq', str(cq), '-b:v', f'{avg_kbps}k',
            '-maxrate', f'{maxrate_kbps}k', '-bufsize', f'{bufsize}k',
            '-rc-lookahead', '32', '-b_ref_mode', 'middle',
            '-tag:v', 'hvc1'
        ]
    elif enc == 'hevc_qsv':
        return [
            '-c:v', 'hevc_qsv', '-preset', 'medium',
            '-global_quality', str(cq), '-b:v', f'{avg_kbps}k',
            '-maxrate', f'{maxrate_kbps}k', '-bufsize', f'{bufsize}k',
            '-tag:v', 'hvc1'
        ]
    elif enc == 'hevc_amf':
        return [
            '-c:v', 'hevc_amf', '-quality', 'balanced',
            '-b:v', f'{avg_kbps}k',
            '-maxrate', f'{maxrate_kbps}k', '-bufsize', f'{bufsize}k',
            '-tag:v', 'hvc1'
        ]
    else:
        return [
            '-c:v', 'libx265', '-preset', 'fast',
            '-crf', str(cq), '-b:v', f'{avg_kbps}k',
            '-maxrate', f'{maxrate_kbps}k', '-bufsize', f'{bufsize}k',
            '-tag:v', 'hvc1'
        ]


def _encode_bdremux_one(f, target_mb, maxrate_kbps, cq, ffmpeg, ffprobe, audio_plan=None):
    name, _ = os.path.splitext(f)
    size_mb = os.path.getsize(f) / (1024 * 1024)

    print(f'\n{CYAN}========================================{RESET}')
    print(f'{BOLD}Обработка: {f} ({size_mb:.0f} МБ){RESET}')
    print(f'{CYAN}========================================{RESET}')

    info = _probe_all(f, ffprobe, ffmpeg)
    duration = info['duration']
    if duration <= 0:
        print(f'{RED}Не определить длительность.{RESET}')
        return False

    inputs, maps, a_args, out_audios = _build_ffmpeg_audio_mapping(
        f, info, audio_plan, ffprobe, ffmpeg, aac_bitrate='256k'
    )

    audio_kbps = 0
    for i, a in enumerate(out_audios):
        if a.get('codec') == 'aac' and (a.get('channels') or 2) <= 2:
            audio_kbps += a.get('bitrate') or 192
            print(f'{GREEN}  дорожка {i + 1}: {_format_audio_desc(a)} → copy{RESET}')
        else:
            audio_kbps += 256
            print(f'{YELLOW}  дорожка {i + 1}: {_format_audio_desc(a)} → AAC 256k{RESET}')

    target_bits = target_mb * 8 * 1024 * 1024
    audio_bits = audio_kbps * 1000 * duration
    video_bits = target_bits - audio_bits
    if video_bits <= 0:
        print(f'{RED}Целевой размер мал.{RESET}')
        return False

    avg_kbps = int(video_bits / duration / 1000)
    bufsize = maxrate_kbps * 2
    print(f'{GREEN}Длительность: {duration:.1f} с ({duration/60:.1f} мин){RESET}')
    print(f'{GREEN}Аудио суммарно: ~{audio_kbps} kbps{RESET}')
    print(f'{GREEN}Видео: avg {avg_kbps} kbps, maxrate {maxrate_kbps} kbps{RESET}')

    output = f'{name}_compressed.mp4'

    enc, hw_args = detect_best_hevc_encoder(ffmpeg)
    print(f'{CYAN}  Энкодер: {BOLD}{enc}{RESET} {"(аппаратное ускорение)" if hw_args else "(CPU)"}{RESET}')

    v_args = _build_encoder_args(enc, cq, avg_kbps, maxrate_kbps, bufsize)
    base_cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-progress', 'pipe:1', '-nostats'] + hw_args
    enc_args = maps + ['-sn'] + v_args + a_args + ['-movflags', '+faststart', output]

    cmd = base_cmd + inputs + enc_args
    rc = _run_ffmpeg_progress(cmd, duration, f)

    if rc != 0 and enc != 'libx265':
        print(f'{YELLOW}  Сбой аппаратного энкодера ({rc}). Откат на CPU (libx265)...{RESET}')
        if os.path.exists(output):
            try:
                os.remove(output)
            except OSError:
                pass
        cpu_v_args = _build_encoder_args('libx265', cq, avg_kbps, maxrate_kbps, bufsize)
        cmd_cpu = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
                   '-progress', 'pipe:1', '-nostats'] + inputs + maps + ['-sn'] + cpu_v_args + a_args + ['-movflags', '+faststart', output]
        rc = _run_ffmpeg_progress(cmd_cpu, duration, f)

    if rc != 0:
        print(f'{RED}Ошибка кодирования ({rc}).{RESET}')
        if os.path.exists(output):
            try:
                os.remove(output)
            except OSError:
                pass
        return False
    return _finish_bdremux(output, size_mb)


def _finish_bdremux(output, size_mb):
    if not os.path.exists(output):
        return False
    out_mb = os.path.getsize(output) / (1024 * 1024)
    ratio = (size_mb / out_mb) if out_mb else 0
    print(f'{GREEN}[OK] {output} — {out_mb:.0f} МБ '
          f'(было {size_mb:.0f} МБ, сжатие ×{ratio:.2f}){RESET}')
    return True


def compress_bdremux():
    ffmpeg = get_tool_path('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    ffprobe = get_tool_path('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    if not _have(ffmpeg):
        print(f'\n{RED}[КРИТИЧЕСКАЯ ОШИБКА] ffmpeg не найден!{RESET}')
        return
    if not _have(ffprobe):
        ffprobe = None

    enc, hw = detect_best_hevc_encoder(ffmpeg)
    print(f'\n{CYAN}{BOLD}═══ Сжатие BDRemux (HEVC H.265, CQ={CQ_VALUE}) ═══{RESET}')
    print(f'{DIM}• Энкодер: {enc} ({"GPU: " + ", ".join(hw) if hw else "CPU/libx265"}){RESET}')
    print(f'{DIM}• Качество: CQ={CQ_VALUE} (визуально без потерь), целевой размер ~{TARGET_MB} МБ/эпизод.{RESET}')

    exts = ('*.mkv', '*.mp4', '*.m2ts', '*.ts', '*.m4v')
    all_files = []
    for e in exts:
        all_files.extend(glob.glob(e))

    THRESHOLD = 4 * 1024 ** 3
    big = sorted({f for f in all_files if os.path.getsize(f) >= THRESHOLD}, key=natural_sort_key)
    if not big:
        print(f'{YELLOW}Файлов размером ≥ 4 ГБ не найдено.{RESET}')
        ans_all = input(f'{BOLD}Показать все доступные видеофайлы любого размера? [Y/n]: {RESET}').strip().lower()
        if ans_all in ('n', 'no', 'н', 'нет', '0'):
            return
        big = sorted(set(all_files), key=natural_sort_key)
        if not big:
            print(f'{YELLOW}Нет видеофайлов.{RESET}')
            return

    print(f'\n{CYAN}Файлы для сжатия:{RESET}')
    for i, f in enumerate(big):
        mb = os.path.getsize(f) / (1024 * 1024)
        print(f'  {GREEN}{i+1}{RESET}. {BOLD}{f}{RESET}  ({mb:.0f} МБ / {mb/1024:.2f} ГБ)')

    audio_plan = _prompt_audio_plan(big, ffprobe, ffmpeg)

    print(f'\n{DIM}Пиковый битрейт (maxrate) ограничивает пики размера видео.{RESET}')
    s = input(f'{BOLD}Пиковый битрейт kbps [Enter = {DEFAULT_MAXRATE}]: {RESET}').strip()
    maxrate_kbps = int(s) if s.isdigit() and int(s) > 0 else DEFAULT_MAXRATE

    print(f'\n{CYAN}Запуск обработки {len(big)} файл(ов)...{RESET}')
    ok = 0
    for f in big:
        try:
            if _encode_bdremux_one(f, TARGET_MB, maxrate_kbps, CQ_VALUE,
                                   ffmpeg, ffprobe, audio_plan):
                ok += 1
        except KeyboardInterrupt:
            print(f'\n{YELLOW}Прервано пользователем.{RESET}')
            break
        except Exception as e:
            print(f'{RED}Ошибка {f}: {e}{RESET}')
    print(f'\n{GREEN}{BOLD}✓ Готово: сжато {ok}/{len(big)} файл(ов).{RESET}')
    send_telegram_notification(f"✅ <b>BDRemux Compress завершен!</b>\nСжато {ok}/{len(big)} файлов.")


# ============================================================================
#  БЛОК 5 — SPLIT
# ============================================================================
def split_video():
    mkvmerge = get_tool_path('mkvmerge.exe' if os.name == 'nt' else 'mkvmerge')
    if not _have(mkvmerge):
        print(f'\n{RED}[КРИТИЧЕСКАЯ ОШИБКА] mkvmerge не найден!{RESET}')
        return

    print(f'\n{CYAN}{BOLD}─── Нарезка видео под лимиты Telegram ───{RESET}')
    print(f'{DIM}• Стандартный лимит: 2000 МБ (рекомендуется 1900 МБ для запаса){RESET}')
    print(f'{DIM}• Лимит с Telegram Premium: 4000 МБ (рекомендуется 3900 МБ){RESET}')
    size_input = input(f'{BOLD}Макс. размер одной части в МБ [Enter = 1900]: {RESET}').strip() or '1900'
    if not size_input.isdigit():
        print(f'{RED}Необходимо ввести числовое значение.{RESET}')
        return

    exts = ('*.mkv', '*.mp4', '*.hevc', '*.avi', '*.m2ts', '*.mov')
    files = []
    for e in exts:
        files.extend(glob.glob(e))
    files = sorted(set(files), key=natural_sort_key)
    if not files:
        print(f'{YELLOW}Нет видео.{RESET}')
        return

    total = len(files)
    for idx, f in enumerate(files, 1):
        name, ext = os.path.splitext(f)
        if re.search(r'-\d{3}$', name):
            continue
        print(f'\n{CYAN}[{idx}/{total}]{RESET} {BOLD}{f}{RESET}')
        output = f'{name}_part.mkv'
        cmd = [mkvmerge, '-o', output, '--split', f'size:{size_input}M', f]
        res = subprocess.run(cmd)
        if res.returncode != 0:
            print(f'{RED}[ОШИБКА] Не удалось нарезать {f}{RESET}')
            continue
        print(f'{GREEN}✓ Успешно разделен на части.{RESET}')
        if ext.lower() != '.mkv':
            ffmpeg = get_tool_path('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
            for part in glob.glob(f'{name}_part-*.mkv'):
                pname, _ = os.path.splitext(part)
                final = f'{pname}{ext}'
                subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
                                '-i', part, '-c', 'copy', final],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    os.remove(part)
                except OSError:
                    pass
        ans = input('Удалить исходный файл? [y/N]: ').strip().lower()
        if ans in ('y', 'yes', 'д', 'да'):
            try:
                os.remove(f)
                print(f'{GREEN}Исходный файл удален.{RESET}')
            except Exception as e:
                print(f'{RED}Не удалось удалить файл: {e}{RESET}')


# ============================================================================
#  БЛОК 6 — MERGE
# ============================================================================
def merge_videos():
    mkvmerge = get_tool_path('mkvmerge.exe' if os.name == 'nt' else 'mkvmerge')
    if not _have(mkvmerge):
        print(f'\n{RED}[КРИТИЧЕСКАЯ ОШИБКА] mkvmerge не найден!{RESET}')
        return

    print(f'\n{CYAN}{BOLD}─── Склейка нескольких видео в один файл ───{RESET}')
    print(f'{DIM}Файлы склеиваются последовательно через mkvmerge без перекодировки и потери качества.{RESET}')
    exts = ('*.mkv', '*.mp4', '*.hevc', '*.avi', '*.m2ts', '*.mov')
    files = []
    for e in exts:
        files.extend(glob.glob(e))
    files = sorted(set(files), key=natural_sort_key)

    if len(files) < 2:
        print(f'{YELLOW}[!] Для склейки нужно минимум 2 видеофайла (найдено: {len(files)}).{RESET}')
        return

    print(f'\n{CYAN}Файлы для склейки (в порядке соединения):{RESET}')
    for i, f in enumerate(files):
        print(f'  {GREEN}{i+1}{RESET}. {f}')

    ans = input(f'\n{BOLD}Начать склейку {len(files)} файлов? [Y/n]: {RESET}').strip().lower()
    if ans in ('n', 'no', 'н', 'нет', '0'):
        print(f'{YELLOW}Склейка отменена.{RESET}')
        return

    _, target_ext = os.path.splitext(files[0])
    output_name = input(f'{BOLD}Имя [merged_video]: {RESET}').strip() or 'merged_video'
    for c in '<>:"/\\|?*':
        output_name = output_name.replace(c, '_')

    out_mkv = f'{output_name}.mkv'
    out_final = f'{output_name}{target_ext}'

    print(f'\n{YELLOW}Попытка 1: mkvmerge...{RESET}')
    cmd = [mkvmerge, '-o', out_mkv, files[0]]
    for f in files[1:]:
        cmd.extend(['+', f])
    res = subprocess.run(cmd)

    if res.returncode != 0:
        print(f'\n{YELLOW}Попытка 2: ffmpeg concat...{RESET}')
        ffmpeg = get_tool_path('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        if _have(ffmpeg):
            with open('concat_list.txt', 'w', encoding='utf-8') as txt:
                for f in files:
                    safe = f.replace("'", "'\\''")
                    txt.write(f"file '{safe}'\n")
            cmd2 = [ffmpeg, '-y', '-hide_banner', '-f', 'concat', '-safe', '0',
                    '-i', 'concat_list.txt', '-c', 'copy', out_final]
            res = subprocess.run(cmd2)
            try:
                os.remove('concat_list.txt')
            except OSError:
                pass
        else:
            print(f'{RED}ffmpeg не найден.{RESET}')

    if res.returncode == 0:
        if os.path.exists(out_mkv) and target_ext.lower() != '.mkv':
            print(f'{YELLOW}Переупаковка в {target_ext}...{RESET}')
            ffmpeg = get_tool_path('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
            subprocess.run([ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
                            '-i', out_mkv, '-c', 'copy', out_final],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                os.remove(out_mkv)
            except OSError:
                pass
        print(f'\n{GREEN}{BOLD}[OK] Склеено в {out_final}{RESET}')
        ans_del = input(f'{BOLD}Удалить исходники? [y/N]: {RESET}').strip().lower()
        if ans_del in ('y', 'yes', 'д', 'да'):
            for f in files:
                try:
                    os.remove(f)
                except OSError:
                    pass
            print(f'{GREEN}Удалены.{RESET}')
    else:
        print(f'\n{RED}[ОШИБКА] Не удалось.{RESET}')


# ============================================================================
#  БЛОК 7 — РАБОТА С ОЗВУЧКАМИ (AUDIO TOOL — БЕЗ ПЕРЕКОДИРОВКИ И FASTSTART)
# ============================================================================
def _parse_multi_indices(raw_str, max_count):
    """
    Парсит ввод вида '1', '1,3,4', '1-3', 'all' в список 0-based индексов.
    Сохраняет порядок, указанный пользователем, без дубликатов.
    """
    s = (raw_str or '').strip().lower()
    if not s:
        return []
    if s in ('all', 'все', '*'):
        return list(range(max_count))

    result = []
    for part in re.split(r'[,\s]+', s):
        if not part:
            continue
        if '-' in part:
            bounds = part.split('-', 1)
            if bounds[0].isdigit() and bounds[1].isdigit():
                start, end = int(bounds[0]), int(bounds[1])
                step = 1 if start <= end else -1
                for i in range(start, end + step, step):
                    if 1 <= i <= max_count and (i - 1) not in result:
                        result.append(i - 1)
        elif part.isdigit():
            i = int(part)
            if 1 <= i <= max_count and (i - 1) not in result:
                result.append(i - 1)
    return result


def _codec_to_ext(codec_name):
    """Определяет родное расширение по названию аудиокодека для -c:a copy."""
    c = (codec_name or '').lower()
    mapping = {
        'aac': '.aac',
        'ac3': '.ac3',
        'eac3': '.eac3',
        'mp3': '.mp3',
        'flac': '.flac',
        'opus': '.opus',
        'vorbis': '.ogg',
        'dts': '.dts',
        'pcm_s16le': '.wav',
        'pcm_s24le': '.wav',
    }
    return mapping.get(c, '.mka')


def _safe_filename(s):
    return re.sub(r'[<>:"/\\|?*]', '_', (s or '').strip())


def _extract_voices_from_videos(files, ffmpeg, ffprobe):
    """1. Вытащить 1 или несколько озвучек из видео (без перекодировки)."""
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    auds = info.get('audio') or []
    if not auds:
        print(f'{YELLOW}В файле {first} не найдено аудиодорожек.{RESET}')
        return

    print(f'\n{CYAN}{BOLD}─── Извлечение озвучек (анализ по {first}) ───{RESET}')
    for a in auds:
        print(f'  {GREEN}{a["a_idx"] + 1}{RESET}. {_format_audio_desc(a)}')

    raw = input(
        f'\n{BOLD}Какие дорожки вытащить? (например 1 или 1,3 или 1-2 или all): {RESET}'
    ).strip()
    selected = _parse_multi_indices(raw, len(auds))
    if not selected:
        print(f'{YELLOW}Ничего не выбрано.{RESET}')
        return

    ext_in = input(
        f'{BOLD}Формат сохранения [Enter = .mka, auto = по кодеку (.aac/.ac3/...)]: {RESET}'
    ).strip().lower()

    use_subfolders = False
    if len(selected) > 1 or len(files) > 1:
        ans_dir = input(
            f'{BOLD}Разложить каждую озвучку в отдельную подпапку? [Y/n]: {RESET}'
        ).strip().lower()
        use_subfolders = ans_dir not in ('n', 'no', 'н', 'нет')

    ok_files = 0
    for f in files:
        f_info = _probe_all(f, ffprobe, ffmpeg)
        f_auds = f_info.get('audio') or []
        stem, _ = os.path.splitext(os.path.basename(f))

        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', f]
        out_paths = []

        for t_idx in selected:
            if t_idx >= len(f_auds):
                print(f'  {YELLOW}[!] В {f} нет дорожки #{t_idx + 1}, пропуск дорожки.{RESET}')
                continue

            track_meta = f_auds[t_idx]
            title_raw = track_meta.get('title') or f'Track_{t_idx + 1}'
            safe_title = _safe_filename(title_raw)

            if not ext_in:
                out_ext = '.mka'
            elif ext_in == 'auto':
                out_ext = _codec_to_ext(track_meta.get('codec'))
            else:
                out_ext = ext_in if ext_in.startswith('.') else f'.{ext_in}'

            if use_subfolders:
                folder_name = _safe_filename(f'{t_idx + 1}_{title_raw}') if track_meta.get('title') else f'Track_{t_idx + 1}'
                os.makedirs(folder_name, exist_ok=True)
                out_file = os.path.join(folder_name, f'{stem}{out_ext}')
            else:
                suffix = f'_{safe_title}' if len(selected) > 1 or track_meta.get('title') else f'_track{t_idx + 1}'
                out_file = f'{stem}{suffix}{out_ext}'

            cmd += ['-map', f'0:a:{t_idx}', '-vn', '-sn', '-dn', '-c:a', 'copy', out_file]
            out_paths.append(out_file)

        if not out_paths:
            continue

        print(f'{CYAN}Извлечение из:{RESET} {f} -> {len(out_paths)} дорожек...')
        r = subprocess.run(cmd)
        if r.returncode == 0:
            ok_files += 1
            for p in out_paths:
                print(f'  {GREEN}✓ {p}{RESET}')
        else:
            print(f'  {RED}✗ Ошибка ffmpeg при извлечении из {f}{RESET}')

    print(f'\n{GREEN}{BOLD}Готово! Обработано видео: {ok_files}/{len(files)}{RESET}')
    try:
        _probe_all_cached.cache_clear()
    except Exception:
        pass


def _extract_subtitles_from_videos(files, ffmpeg, ffprobe):
    """Вытащить 1 или несколько дорожек субтитров из видео (без перекодировки)."""
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    subs = info.get('subtitles') or []
    if not subs:
        print(f'{YELLOW}В файле {first} не найдено субтитров.{RESET}')
        return

    print(f'\n{CYAN}{BOLD}─── Извлечение субтитров (анализ по {first}) ───{RESET}')
    for s in subs:
        print(f'  {GREEN}{s["s_idx"] + 1}{RESET}. {_format_subtitle_desc(s)}')

    raw = input(
        f'\n{BOLD}Какие субтитры вытащить? (например 1 или 1,2 или 1-2 или all): {RESET}'
    ).strip()
    selected = _parse_multi_indices(raw, len(subs))
    if not selected:
        print(f'{YELLOW}Ничего не выбрано.{RESET}')
        return

    use_subfolders = False
    if len(selected) > 1 or len(files) > 1:
        ans_dir = input(
            f'{BOLD}Разложить каждые субтитры в отдельную подпапку? [Y/n]: {RESET}'
        ).strip().lower()
        use_subfolders = ans_dir not in ('n', 'no', 'н', 'нет')

    ok_files = 0
    for f in files:
        f_info = _probe_all(f, ffprobe, ffmpeg)
        f_subs = f_info.get('subtitles') or []
        stem, _ = os.path.splitext(os.path.basename(f))

        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', f]
        out_paths = []

        for s_idx in selected:
            if s_idx >= len(f_subs):
                continue
            track_meta = f_subs[s_idx]
            title_raw = track_meta.get('title') or f'Sub_{s_idx + 1}'
            safe_title = _safe_filename(title_raw)
            out_ext = _sub_codec_to_ext(track_meta.get('codec'))

            if use_subfolders:
                folder_name = _safe_filename(f'Sub_{s_idx + 1}_{title_raw}') if track_meta.get('title') else f'Sub_{s_idx + 1}'
                os.makedirs(folder_name, exist_ok=True)
                out_file = os.path.join(folder_name, f'{stem}{out_ext}')
            else:
                suffix = f'_{safe_title}' if len(selected) > 1 or track_meta.get('title') else f'_sub{s_idx + 1}'
                out_file = f'{stem}{suffix}{out_ext}'

            cmd += ['-map', f'0:s:{s_idx}', '-vn', '-an', '-dn', '-c:s', 'copy', out_file]
            out_paths.append(out_file)

        if not out_paths:
            continue

        print(f'{CYAN}Извлечение из:{RESET} {f} -> {len(out_paths)} дорожек...')
        r = subprocess.run(cmd)
        if r.returncode == 0:
            ok_files += 1
            for p in out_paths:
                print(f'  {GREEN}✓ {p}{RESET}')
        else:
            print(f'  {RED}✗ Ошибка ffmpeg при извлечении субтитров из {f}{RESET}')

    print(f'\n{GREEN}{BOLD}Готово! Извлечены субтитры из {ok_files}/{len(files)} видео.{RESET}')
    try:
        _probe_all_cached.cache_clear()
    except Exception:
        pass


def _add_external_audio_and_subs(files, ffmpeg, ffprobe, mkvmerge):
    """Вшить внешние озвучки и/или внешние субтитры в видео без перекодировки."""
    print(f'\n{CYAN}{BOLD}─── Вшивка внешних дорожек (без перекодировки видео) ───{RESET}')
    print(f'{BOLD}Что вы хотите вшить?{RESET}')
    print(f'  {GREEN}1{RESET}. Только внешние озвучки (.mka, .aac, .mp3, .flac и др.)')
    print(f'  {GREEN}2{RESET}. Только внешние субтитры (.ass, .srt, .vtt и др.)')
    print(f'  {GREEN}3{RESET}. И озвучки, И субтитры одновременно')

    what_in = input(f'{BOLD}Выбор [1-3, Enter = 3]: {RESET}').strip() or '3'

    need_audio = what_in in ('1', '3')
    need_subs  = what_in in ('2', '3')

    voice_packs = []
    if need_audio:
        ext_sources = _find_external_audio_sources('.')
        if not ext_sources:
            print(f'{YELLOW}Внешние аудиофайлы не найдены.{RESET}')
            if not need_subs:
                return
        else:
            folders = list(ext_sources.keys())
            print(f'\n{CYAN}{BOLD}── Доступные источники внешних озвучек ──{RESET}')
            for i, fld in enumerate(folders, 1):
                flist = ext_sources[fld]
                sample = os.path.basename(flist[0]) if flist else ''
                print(f'  {GREEN}{i}{RESET}. {BOLD}{fld}{RESET} — {len(flist)} файл(ов) {DIM}({sample}...){RESET}')

            raw_sel = input(f'{BOLD}Выберите папку(и) с озвучками (например 1 или 1,2 или all): {RESET}').strip()
            chosen_indices = _parse_multi_indices(raw_sel, len(folders))
            for idx in chosen_indices:
                fld = folders[idx]
                aud_list = ext_sources[fld]
                default_title = '' if fld == '[Текущая папка]' else os.path.basename(fld)

                matched_map = _match_videos_with_audios(files, aud_list)
                print(f'  {CYAN}Озвучка "{fld}": сопоставлено {len(matched_map)}/{len(files)} видео.{RESET}')
                if not matched_map:
                    continue

                t_in = input(f'  {BOLD}Название дорожки для "{fld}" [Enter = "{default_title}"]: {RESET}').strip()
                v_title = t_in if t_in else default_title
                l_in = input(f'  {BOLD}Язык озвучки 3 буквы [Enter = rus]: {RESET}').strip().lower() or 'rus'
                voice_packs.append({
                    'label': fld,
                    'title': v_title,
                    'lang': l_in,
                    'map': matched_map,
                })

    sub_packs = []
    if need_subs:
        ext_sub_sources = _find_external_subtitle_sources('.')
        if not ext_sub_sources:
            print(f'{YELLOW}Внешние файлы субтитров (.ass, .srt, .vtt) не найдены.{RESET}')
            if not voice_packs:
                return
        else:
            folders_s = list(ext_sub_sources.keys())
            print(f'\n{CYAN}{BOLD}── Доступные источники внешних субтитров ──{RESET}')
            for i, fld in enumerate(folders_s, 1):
                flist = ext_sub_sources[fld]
                sample = os.path.basename(flist[0]) if flist else ''
                print(f'  {GREEN}{i}{RESET}. {BOLD}{fld}{RESET} — {len(flist)} файл(ов) {DIM}({sample}...){RESET}')

            raw_sel_s = input(f'{BOLD}Выберите папку(и) с субтитрами (например 1 или all): {RESET}').strip()
            chosen_indices_s = _parse_multi_indices(raw_sel_s, len(folders_s))
            for idx in chosen_indices_s:
                fld = folders_s[idx]
                sub_list = ext_sub_sources[fld]
                default_title = '' if fld == '[Текущая папка]' else os.path.basename(fld)

                matched_map = _match_videos_with_audios(files, sub_list)
                print(f'  {CYAN}Субтитры "{fld}": сопоставлено {len(matched_map)}/{len(files)} видео.{RESET}')
                if not matched_map:
                    continue

                t_in = input(f'  {BOLD}Название субтитров для "{fld}" [Enter = "{default_title or "Русские субтитры"}"]: {RESET}').strip()
                s_title = t_in if t_in else (default_title or 'Русские субтитры')
                l_in = input(f'  {BOLD}Язык субтитров [Enter = rus]: {RESET}').strip().lower() or 'rus'
                is_def = input(f'  {BOLD}Сделать эти субтитры активными по умолчанию? [y/N]: {RESET}').strip().lower() in ('y', 'yes', 'д', 'да')

                sub_packs.append({
                    'label': fld,
                    'title': s_title,
                    'lang': l_in,
                    'default': is_def,
                    'map': matched_map,
                })

    if not voice_packs and not sub_packs:
        print(f'{RED}Нет сопоставленных файлов для вшивки.{RESET}')
        return

    # Check for fonts in directory
    fonts_found = _find_external_fonts('.')
    if fonts_found:
        print(f'\n{MAGENTA}✨ Обнаружено {len(fonts_found)} шрифтов (.ttf/.otf). Они будут автоматически прикреплены в MKV!{RESET}')

    print(f'\n{BOLD}Режим добавления дорожек:{RESET}')
    print(f'  {GREEN}1{RESET}. Добавить ПЕРВЫМИ дорожками + сохранить родные дорожки видео {DIM}(по умолчанию){RESET}')
    print(f'  {GREEN}2{RESET}. Добавить В КОНЕЦ (после родных дорожек)')
    print(f'  {GREEN}3{RESET}. ЗАМЕНИТЬ родные (оставить только добавляемые внешние дорожки)')
    mode_in = input(f'{BOLD}Выбор [1-3, Enter = 1]: {RESET}').strip() or '1'

    del_ext = input(
        f'{BOLD}Удалить исходные внешние файлы после успешной вшивки? [y/N]: {RESET}'
    ).strip().lower() in ('y', 'yes', 'д', 'да')

    have_mkvmerge = _have(mkvmerge)

    ok_count = 0
    for f in files:
        f_info = _probe_all(f, ffprobe, ffmpeg)
        orig_auds = f_info.get('audio') or []
        orig_subs = f_info.get('subtitles') or []
        stem, orig_ext = os.path.splitext(f)

        ext_audios = []
        for vp in voice_packs:
            a_file = vp['map'].get(f)
            if a_file and os.path.exists(a_file):
                ext_audios.append({'path': a_file, 'title': vp['title'], 'lang': vp['lang']})

        ext_subtitles = []
        for sp in sub_packs:
            s_file = sp['map'].get(f)
            if s_file and os.path.exists(s_file):
                ext_subtitles.append({'path': s_file, 'title': sp['title'], 'lang': sp['lang'], 'default': sp['default']})

        if not ext_audios and not ext_subtitles:
            continue

        # MKV container is ideal for anime with soft-subs/fonts
        out_ext = '.mkv'
        tmp_out = f'{stem}.mux_tmp{out_ext}'
        final_out = f'{stem}{out_ext}'

        # Prefer mkvmerge if available for lightning fast and robust muxing
        used_mkvmerge = False
        if have_mkvmerge and out_ext == '.mkv':
            cmd_mkv = [mkvmerge, '-o', tmp_out]
            # 1. Video and existing tracks from original file
            if mode_in == '3':
                # Replace: only video from 0
                cmd_mkv += ['-d', '0', '-A', '-S', f]
            else:
                cmd_mkv += [f]

            # 2. Add external audios
            for ea in ext_audios:
                track_opts = []
                if ea['title']:
                    track_opts += ['--track-name', f'0:{ea["title"]}']
                if ea['lang']:
                    track_opts += ['--language', f'0:{ea["lang"]}']
                cmd_mkv += track_opts + [ea['path']]

            # 3. Add external subtitles
            for es in ext_subtitles:
                track_opts = []
                if es['title']:
                    track_opts += ['--track-name', f'0:{es["title"]}']
                if es['lang']:
                    track_opts += ['--language', f'0:{es["lang"]}']
                if es.get('default'):
                    track_opts += ['--default-track-flag', '0:yes']
                cmd_mkv += track_opts + [es['path']]

            # 4. Attach fonts
            for font_p in fonts_found:
                cmd_mkv += ['--attach-file', font_p]

            r = subprocess.run(cmd_mkv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
            if r.returncode in (0, 1) and os.path.exists(tmp_out):
                used_mkvmerge = True

        # Fallback to ffmpeg -c copy
        if not used_mkvmerge:
            cmd_ff = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', f]
            input_idx = 1
            for ea in ext_audios:
                cmd_ff += ['-i', ea['path']]
                input_idx += 1
            for es in ext_subtitles:
                cmd_ff += ['-i', es['path']]
                input_idx += 1

            cmd_ff += ['-map', '0:v:0']
            meta_args = []
            cur_a = 0
            cur_s = 0

            # Audio mapping
            if mode_in in ('1', '3'):
                for i, ea in enumerate(ext_audios, 1):
                    cmd_ff += ['-map', f'{i}:a:0']
                    if ea['title']:
                        meta_args += [f'-metadata:s:a:{cur_a}', f'title={ea["title"]}']
                    if ea['lang']:
                        meta_args += [f'-metadata:s:a:{cur_a}', f'language={ea["lang"]}']
                    cur_a += 1
                if mode_in == '1':
                    for oa in orig_auds:
                        cmd_ff += ['-map', f'0:a:{oa["a_idx"]}']
                        cur_a += 1
            elif mode_in == '2':
                for oa in orig_auds:
                    cmd_ff += ['-map', f'0:a:{oa["a_idx"]}']
                    cur_a += 1
                for i, ea in enumerate(ext_audios, 1):
                    cmd_ff += ['-map', f'{i}:a:0']
                    if ea['title']:
                        meta_args += [f'-metadata:s:a:{cur_a}', f'title={ea["title"]}']
                    if ea['lang']:
                        meta_args += [f'-metadata:s:a:{cur_a}', f'language={ea["lang"]}']
                    cur_a += 1

            # Subtitle mapping
            sub_start_idx = 1 + len(ext_audios)
            if mode_in in ('1', '3'):
                for i, es in enumerate(ext_subtitles, sub_start_idx):
                    cmd_ff += ['-map', f'{i}:s:0']
                    if es['title']:
                        meta_args += [f'-metadata:s:s:{cur_s}', f'title={es["title"]}']
                    if es['lang']:
                        meta_args += [f'-metadata:s:s:{cur_s}', f'language={es["lang"]}']
                    if es.get('default'):
                        meta_args += [f'-disposition:s:{cur_s}', 'default']
                    cur_s += 1
                if mode_in == '1':
                    for os_item in orig_subs:
                        cmd_ff += ['-map', f'0:s:{os_item["s_idx"]}']
                        cur_s += 1
            elif mode_in == '2':
                for os_item in orig_subs:
                    cmd_ff += ['-map', f'0:s:{os_item["s_idx"]}']
                    cur_s += 1
                for i, es in enumerate(ext_subtitles, sub_start_idx):
                    cmd_ff += ['-map', f'{i}:s:0']
                    if es['title']:
                        meta_args += [f'-metadata:s:s:{cur_s}', f'title={es["title"]}']
                    if es['lang']:
                        meta_args += [f'-metadata:s:s:{cur_s}', f'language={es["lang"]}']
                    if es.get('default'):
                        meta_args += [f'-disposition:s:{cur_s}', 'default']
                    cur_s += 1

            cmd_ff += ['-c', 'copy'] + meta_args + [tmp_out]
            r = subprocess.run(cmd_ff, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')

        if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 1000:
            try:
                os.replace(tmp_out, final_out)
                if os.path.abspath(f) != os.path.abspath(final_out):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                if del_ext:
                    for ea in ext_audios:
                        try:
                            if os.path.exists(ea['path']):
                                os.remove(ea['path'])
                        except OSError:
                            pass
                    for es in ext_subtitles:
                        try:
                            if os.path.exists(es['path']):
                                os.remove(es['path'])
                        except OSError:
                            pass
                ok_count += 1
                print(f'  {GREEN}✓ Готово: {final_out}{RESET}')
            except OSError as e:
                print(f'  {RED}✗ Ошибка замены файла {f}: {e}{RESET}')
        else:
            print(f'  {RED}✗ Ошибка при сборке {f}{RESET}')
            try:
                if os.path.exists(tmp_out):
                    os.remove(tmp_out)
            except OSError:
                pass

    print(f'\n{GREEN}{BOLD}Успешно обновлено файлов: {ok_count}/{len(files)}{RESET}')
    try:
        _probe_all_cached.cache_clear()
    except Exception:
        pass


def _hardsub_videos(files, ffmpeg, ffprobe):
    """Вшить субтитры намертво в видеоряд (Hardsub / Burn-in) с авто GPU/CPU энкодером."""
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    subs = info.get('subtitles') or []
    ext_sub_sources = _find_external_subtitle_sources('.')

    print(f'\n{CYAN}{BOLD}═══ Вшивание субтитров намертво (Hardsub) ═══{RESET}')
    print(f'{BOLD}Выберите источник субтитров:{RESET}')

    sub_source_mode = 'internal'
    chosen_sub_idx = 0
    external_sub_map = {}

    if subs:
        print(f'  {GREEN}1{RESET}. Встроенные субтитры из видео (найдено: {len(subs)})')
    if ext_sub_sources:
        print(f'  {GREEN}2{RESET}. Внешний файл субтитров (.ass, .srt из папки)')

    s_src = input(f'{BOLD}Выбор [1-2, Enter = 1]: {RESET}').strip() or '1'
    if s_src == '2' and ext_sub_sources:
        sub_source_mode = 'external'
        folders_s = list(ext_sub_sources.keys())
        print(f'\n{BOLD}Выберите источник внешних субтитров:{RESET}')
        for i, fld in enumerate(folders_s, 1):
            print(f'  {GREEN}{i}{RESET}. {fld} ({len(ext_sub_sources[fld])} файлов)')
        s_idx_in = input(f'{BOLD}Номер папки [1-{len(folders_s)}]: {RESET}').strip()
        idx_s = (int(s_idx_in) - 1) if s_idx_in.isdigit() and 1 <= int(s_idx_in) <= len(folders_s) else 0
        sub_list = ext_sub_sources[folders_s[idx_s]]
        external_sub_map = _match_videos_with_audios(files, sub_list)
        print(f'  {GREEN}Сопоставлено субтитров: {len(external_sub_map)}/{len(files)}{RESET}')
    else:
        if not subs:
            print(f'{YELLOW}Встроенных субтитров нет.{RESET}')
            return
        if len(subs) > 1:
            print(f'\n{BOLD}Встроенные субтитры:{RESET}')
            for s in subs:
                print(f'  {GREEN}{s["s_idx"] + 1}{RESET}. {_format_subtitle_desc(s)}')
            raw_s = input(f'{BOLD}Номер дорожки для вшивки [1-{len(subs)}]: {RESET}').strip()
            chosen_sub_idx = (int(raw_s) - 1) if raw_s.isdigit() and 1 <= int(raw_s) <= len(subs) else 0

    # Audio choice: keep all or pick one
    auds = info.get('audio') or []
    audio_map_choice = 'all'
    chosen_aud_idx = 0
    if len(auds) > 1:
        print(f'\n{BOLD}Какую озвучку оставить в hardsub-видео?{RESET}')
        print(f'  {GREEN}1{RESET}. Оставить ВСЕ аудиодорожки как есть {DIM}(-c:a copy){RESET}')
        for a in auds:
            print(f'  {GREEN}{a["a_idx"] + 2}{RESET}. {_format_audio_desc(a)}')
        raw_a = input(f'{BOLD}Выбор [1-{len(auds) + 1}, Enter = 1]: {RESET}').strip() or '1'
        if raw_a.isdigit() and int(raw_a) > 1:
            chosen_aud_idx = int(raw_a) - 2
            audio_map_choice = 'single'

    # Encoder detection
    best_enc, hw = detect_best_hevc_encoder(ffmpeg)
    print(f'\n{DIM}Определен видеоэнкодер: {best_enc} ({", ".join(hw) if hw else "CPU"}){RESET}')

    print(f'{BOLD}Выберите видеоэнкодер:{RESET}')
    print(f'  {GREEN}1{RESET}. Авто GPU/быстрый ({best_enc})')
    print(f'  {GREEN}2{RESET}. CPU x264 (максимальная совместимость для всех устройств)')
    enc_in = input(f'{BOLD}Выбор [1-2, Enter = 1]: {RESET}').strip() or '1'
    vcodec = best_enc if enc_in == '1' else 'libx264'

    confirm = input(f'\n{BOLD}Запустить Hardsub для {len(files)} файл(ов)? [y/N]: {RESET}').strip().lower()
    if confirm not in ('y', 'yes', 'д', 'да'):
        print(f'{YELLOW}Отмена.{RESET}')
        return

    ok_count = 0
    for f in files:
        stem, ext = os.path.splitext(f)
        tmp_out = f'{stem}.hardsub.tmp.mp4'
        final_out = f'{stem}.hardsub.mp4'

        # Build subtitle filter string with escaped backslashes and colons
        if sub_source_mode == 'external':
            sub_file = external_sub_map.get(f)
            if not sub_file or not os.path.exists(sub_file):
                print(f'{YELLOW}[!] Для {f} не найден файл субтитров, пропуск.{RESET}')
                continue
            esc_sub = os.path.abspath(sub_file).replace('\\', '/').replace(':', r'\:')
            vf_sub = f"subtitles='{esc_sub}'"
        else:
            esc_video = os.path.abspath(f).replace('\\', '/').replace(':', r'\:')
            vf_sub = f"subtitles='{esc_video}':si={chosen_sub_idx}"

        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', f]
        cmd += ['-vf', vf_sub]

        if vcodec == 'hevc_nvenc':
            cmd += ['-c:v', 'hevc_nvenc', '-preset', 'p5', '-cq', '20']
        elif vcodec == 'h264_nvenc':
            cmd += ['-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '20']
        elif vcodec == 'hevc_qsv':
            cmd += ['-c:v', 'hevc_qsv', '-global_quality', '20']
        elif vcodec == 'h264_qsv':
            cmd += ['-c:v', 'h264_qsv', '-global_quality', '20']
        elif vcodec == 'libx265':
            cmd += ['-c:v', 'libx265', '-crf', '20', '-preset', 'fast']
        else:
            cmd += ['-c:v', 'libx264', '-crf', '19', '-preset', 'fast']

        if audio_map_choice == 'single':
            cmd += ['-map', '0:v:0', '-map', f'0:a:{chosen_aud_idx}']
        else:
            cmd += ['-map', '0:v:0', '-map', '0:a?']

        cmd += ['-c:a', 'copy', '-sn', '-movflags', '+faststart', tmp_out]

        print(f'{CYAN}Рендеринг Hardsub:{RESET} {f} -> {final_out}...')
        r = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        if r.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 1000:
            try:
                os.replace(tmp_out, final_out)
                ok_count += 1
                print(f'  {GREEN}✓ Готово: {final_out}{RESET}')
            except OSError as e:
                print(f'  {RED}✗ Ошибка: {e}{RESET}')
        else:
            err_tail = (r.stderr or '')[-300:].replace('\n', ' ')
            print(f'  {RED}✗ Ошибка ffmpeg: {err_tail}{RESET}')
            try:
                if os.path.exists(tmp_out):
                    os.remove(tmp_out)
            except OSError:
                pass

    print(f'\n{GREEN}{BOLD}Hardsub завершен для {ok_count}/{len(files)} видео!{RESET}')


def _filter_clean_tracks(files, ffmpeg, ffprobe):
    """Оставить только нужные дорожки аудио и субтитров (вырезать лишние языки без перекодировки)."""
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    auds = info.get('audio') or []
    subs = info.get('subtitles') or []

    print(f'\n{CYAN}{BOLD}─── Очистка дорожек (анализ по {first}) ───{RESET}')
    if auds:
        print(f'{BOLD}Аудиодорожки в контейнере:{RESET}')
        for a in auds:
            print(f'  {GREEN}{a["a_idx"] + 1}{RESET}. {_format_audio_desc(a)}')
    else:
        print(f'  {YELLOW}Аудиодорожек не найдено.{RESET}')

    if subs:
        print(f'\n{BOLD}Субтитры в контейнере:{RESET}')
        for s in subs:
            print(f'  {GREEN}{s["s_idx"] + 1}{RESET}. {_format_subtitle_desc(s)}')
    else:
        print(f'  {YELLOW}Субтитров не найдено.{RESET}')

    raw_a = input(f'\n{BOLD}Какие аудиодорожки оставить? (например 1 или 1,2 или all): {RESET}').strip()
    keep_auds = _parse_multi_indices(raw_a, len(auds)) if raw_a and raw_a != 'all' else list(range(len(auds)))

    raw_s = input(f'{BOLD}Какие субтитры оставить? (например 1 или none или all): {RESET}').strip()
    if raw_s.lower() in ('none', '0', 'нет', 'убрать'):
        keep_subs = []
    elif raw_s and raw_s != 'all':
        keep_subs = _parse_multi_indices(raw_s, len(subs))
    else:
        keep_subs = list(range(len(subs)))

    confirm = input(f'\n{BOLD}Применить фильтрацию к {len(files)} файл(ам)? [y/N]: {RESET}').strip().lower()
    if confirm not in ('y', 'yes', 'д', 'да'):
        print(f'{YELLOW}Отмена.{RESET}')
        return

    ok_count = 0
    for f in files:
        stem, ext = os.path.splitext(f)
        tmp_out = f'{stem}.clean_tmp{ext}'

        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', f, '-map', '0:v:0']
        if keep_auds:
            for k_a in keep_auds:
                cmd += ['-map', f'0:a:{k_a}']
        else:
            cmd += ['-an']

        if keep_subs:
            for k_s in keep_subs:
                cmd += ['-map', f'0:s:{k_s}']
        else:
            cmd += ['-sn']

        cmd += ['-map_metadata', '0', '-c', 'copy', tmp_out]

        r = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        if r.returncode == 0 and os.path.exists(tmp_out):
            try:
                os.replace(tmp_out, f)
                ok_count += 1
                print(f'  {GREEN}✓ Очищено: {f}{RESET}')
            except OSError as e:
                print(f'  {RED}✗ Ошибка замены: {e}{RESET}')
        else:
            print(f'  {RED}✗ Ошибка ffmpeg при обработке {f}{RESET}')
            try:
                if os.path.exists(tmp_out):
                    os.remove(tmp_out)
            except OSError:
                pass

    print(f'\n{GREEN}{BOLD}Готово! Обработано файлов: {ok_count}/{len(files)}{RESET}')
    try:
        _probe_all_cached.cache_clear()
    except Exception:
        pass


def _remove_voices_or_subs_from_videos(files, ffmpeg, ffprobe):
    """Удалить выбранные озвучки или субтитры из видео (-c copy)."""
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    auds = info.get('audio') or []
    subs = info.get('subtitles') or []

    print(f'\n{BOLD}Что вы хотите удалить?{RESET}')
    print(f'  {GREEN}1{RESET}. Аудиодорожки (озвучки)')
    print(f'  {GREEN}2{RESET}. Субтитры')
    print(f'  {GREEN}3{RESET}. Удалить ВСЕ субтитры сразу')

    target_type = input(f'{BOLD}Выбор [1-3]: {RESET}').strip()

    if target_type == '1':
        _remove_voices_from_videos(files, ffmpeg, ffprobe)
    elif target_type == '2':
        if not subs:
            print(f'{YELLOW}В файле нет субтитров.{RESET}')
            return
        print(f'\n{CYAN}{BOLD}Субтитры в видео:{RESET}')
        for s in subs:
            print(f'  {GREEN}{s["s_idx"] + 1}{RESET}. {_format_subtitle_desc(s)}')
        raw = input(f'\n{BOLD}Какие субтитры УДАЛИТЬ? (например 1 или 1,2): {RESET}').strip()
        to_del = set(_parse_multi_indices(raw, len(subs)))
        if not to_del:
            return

        ok_count = 0
        for f in files:
            f_info = _probe_all(f, ffprobe, ffmpeg)
            f_subs = f_info.get('subtitles') or []
            keep_s = [s['s_idx'] for s in f_subs if s['s_idx'] not in to_del]
            stem, ext = os.path.splitext(f)
            tmp_out = f'{stem}.sub_del_tmp{ext}'

            cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', f, '-map', '0:v:0', '-map', '0:a?']
            if keep_s:
                for k in keep_s:
                    cmd += ['-map', f'0:s:{k}']
            else:
                cmd += ['-sn']
            cmd += ['-map_metadata', '0', '-c', 'copy', tmp_out]

            r = subprocess.run(cmd)
            if r.returncode == 0 and os.path.exists(tmp_out):
                try:
                    os.replace(tmp_out, f)
                    ok_count += 1
                    print(f'  {GREEN}✓ Субтитры удалены: {f}{RESET}')
                except OSError:
                    pass
        print(f'\n{GREEN}{BOLD}Готово! Обработано файлов: {ok_count}/{len(files)}{RESET}')
    elif target_type == '3':
        ok_count = 0
        for f in files:
            stem, ext = os.path.splitext(f)
            tmp_out = f'{stem}.nosub_tmp{ext}'
            cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', f, '-map', '0:v:0', '-map', '0:a?', '-sn', '-c', 'copy', tmp_out]
            r = subprocess.run(cmd)
            if r.returncode == 0 and os.path.exists(tmp_out):
                try:
                    os.replace(tmp_out, f)
                    ok_count += 1
                    print(f'  {GREEN}✓ Все субтитры удалены: {f}{RESET}')
                except OSError:
                    pass
        print(f'\n{GREEN}{BOLD}Готово! Обработано файлов: {ok_count}/{len(files)}{RESET}')


def _rename_track_in_videos(files, ffmpeg, ffprobe):
    """Переименовать дорожку (аудио или субтитры)."""
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    auds = info.get('audio') or []
    subs = info.get('subtitles') or []

    print(f'\n{BOLD}Какую дорожку переименовать?{RESET}')
    print(f'  {GREEN}1{RESET}. Аудиодорожку (озвучку)')
    print(f'  {GREEN}2{RESET}. Субтитры')
    t_choice = input(f'{BOLD}Выбор [1-2, Enter = 1]: {RESET}').strip() or '1'

    if t_choice == '1':
        _rename_voice_in_videos(files, ffmpeg, ffprobe)
        return

    if not subs:
        print(f'{YELLOW}В файле нет субтитров.{RESET}')
        return

    print(f'\n{CYAN}{BOLD}Субтитры в видео:{RESET}')
    for s in subs:
        print(f'  {GREEN}{s["s_idx"] + 1}{RESET}. {_format_subtitle_desc(s)}')

    raw = input(f'{BOLD}Номер субтитров для переименования [1-{len(subs)}]: {RESET}').strip()
    if not (raw.isdigit() and 1 <= int(raw) <= len(subs)):
        return
    target_idx = int(raw) - 1

    cur_title = subs[target_idx].get('title') or ''
    cur_lang = subs[target_idx].get('lang') or 'und'

    new_title = input(f'{BOLD}Новое название субтитров #{target_idx + 1} [{cur_title}]: {RESET}').strip() or cur_title
    new_lang = input(f'{BOLD}Код языка (3 буквы) [{cur_lang}]: {RESET}').strip().lower() or cur_lang

    mkvpropedit = get_tool_path('mkvpropedit.exe' if os.name == 'nt' else 'mkvpropedit')
    have_mkvpropedit = _have(mkvpropedit)

    ok_count = 0
    for f in files:
        stem, ext = os.path.splitext(f)
        if ext.lower() == '.mkv' and have_mkvpropedit:
            cmd = [mkvpropedit, f, '--edit', f'track:s{target_idx + 1}', '--set', f'name={new_title}']
            if new_lang:
                cmd += ['--set', f'language={new_lang}']
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
            if r.returncode == 0:
                ok_count += 1
                print(f'  {GREEN}✓ Мгновенно обновлено (mkvpropedit): {f}{RESET}')
                continue

        tmp_out = f'{stem}.sub_ren_tmp{ext}'
        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-i', f, '-map', '0', '-map_metadata', '0', '-c', 'copy',
               f'-metadata:s:s:{target_idx}', f'title={new_title}']
        if new_lang:
            cmd += [f'-metadata:s:s:{target_idx}', f'language={new_lang}']
        cmd.append(tmp_out)

        r = subprocess.run(cmd)
        if r.returncode == 0 and os.path.exists(tmp_out):
            try:
                os.replace(tmp_out, f)
                ok_count += 1
                print(f'  {GREEN}✓ Обновлено: {f}{RESET}')
            except OSError:
                pass

    print(f'\n{GREEN}{BOLD}Готово! Переименовано в {ok_count}/{len(files)} файл(ах).{RESET}')
    try:
        _probe_all_cached.cache_clear()
    except Exception:
        pass


def audio_voice_manager():
    print(f'\n{CYAN}{BOLD}═══ Менеджер озвучек и субтитров ═══{RESET}')
    print(f'{DIM}Настройка дорожек звука и субтитров без перекодировки видеоряда.{RESET}')

    ffmpeg   = get_tool_path('ffmpeg.exe'   if os.name == 'nt' else 'ffmpeg')
    ffprobe  = get_tool_path('ffprobe.exe'  if os.name == 'nt' else 'ffprobe')
    mkvmerge = get_tool_path('mkvmerge.exe' if os.name == 'nt' else 'mkvmerge')

    if not _have(ffmpeg):
        print(f'{RED}[!] ffmpeg не найден!{RESET}')
        return
    if not _have(ffprobe):
        ffprobe = None

    exts = ('*.mkv', '*.mp4', '*.avi', '*.mov', '*.m4v', '*.ts', '*.webm')
    files = []
    for e in exts:
        files.extend(glob.glob(e))
    files = sorted(set(files), key=natural_sort_key)
    if not files:
        print(f'{YELLOW}Видеофайлы в текущей папке не найдены.{RESET}')
        return

    # Краткая сводка по первому файлу сразу при входе
    first_info = _probe_all(files[0], ffprobe, ffmpeg)
    first_auds = first_info.get('audio') or []
    first_subs = first_info.get('subtitles') or []
    print(f'\n{CYAN}┌────────────────────── ОБЗОР ДОРОЖЕК ──────────────────────┐{RESET}')
    print(f'│ Видео в папке: {str(len(files)) + " шт.":<15} Пример: {os.path.basename(files[0])[:30]:<30} │')
    print(f'{CYAN}├───────────────────────────────────────────────────────────┤{RESET}')
    print(f'{BOLD}  Аудиодорожки ({len(first_auds)}):{RESET}')
    for a in first_auds:
        print(f'    {DIM}• {_format_audio_desc(a)}{RESET}')
    if first_subs:
        print(f'{BOLD}  Субтитры ({len(first_subs)}):{RESET}')
        for s in first_subs:
            print(f'    {DIM}• {_format_subtitle_desc(s)}{RESET}')
    else:
        print(f'{DIM}  Субтитры: нет встроенных{RESET}')
    print(f'{CYAN}└───────────────────────────────────────────────────────────┘{RESET}')

    print(f'\n{BOLD}Выберите действие:{RESET}')
    print(f'  {GREEN}1{RESET}. {BOLD}Вшить внешние озвучки и/или субтитры{RESET} {DIM}(Softsub / -c copy){RESET}')
    print(f'     {DIM}↳ Вшить .mka/.aac или .ass/.srt. Дорожки можно переключать в плеере.{RESET}')
    print(f'  {GREEN}2{RESET}. {BOLD}Вшить субтитры намертво в картинку{RESET} {DIM}(Hardsub / Burn-in){RESET}')
    print(f'     {DIM}↳ Впечь субтитры в видео с сохранением стилей и шрифтов (для ТВ и веба).{RESET}')
    print(f'  {GREEN}3{RESET}. {BOLD}Очистить / Оставить только нужные дорожки{RESET}')
    print(f'     {DIM}↳ Удалить лишние дорожки и языки, оставив только выбранные.{RESET}')
    print(f'  {GREEN}4{RESET}. {BOLD}Вытащить озвучки из видео{RESET} {DIM}(сохранить как .mka/.aac без потерь){RESET}')
    print(f'  {GREEN}5{RESET}. {BOLD}Вытащить субтитры из видео{RESET} {DIM}(сохранить как .ass/.srt){RESET}')
    print(f'  {GREEN}6{RESET}. {BOLD}Удалить озвучки или субтитры{RESET} {DIM}(быстро убрать дорожку){RESET}')
    print(f'  {GREEN}7{RESET}. {BOLD}Переименовать дорожку{RESET} {DIM}(изменить название студии или код языка){RESET}')
    print(f'  {GREEN}8{RESET}. {BOLD}Показать полный список всех дорожек во всех видео{RESET}')
    print(f'  {RED}0{RESET}. Назад в главное меню')

    act = input(f'\n{BOLD}Действие [0-8]: {RESET}').strip()

    if act == '1':
        _add_external_audio_and_subs(files, ffmpeg, ffprobe, mkvmerge)
    elif act == '2':
        _hardsub_videos(files, ffmpeg, ffprobe)
    elif act == '3':
        _filter_clean_tracks(files, ffmpeg, ffprobe)
    elif act == '4':
        _extract_voices_from_videos(files, ffmpeg, ffprobe)
    elif act == '5':
        _extract_subtitles_from_videos(files, ffmpeg, ffprobe)
    elif act == '6':
        _remove_voices_or_subs_from_videos(files, ffmpeg, ffprobe)
    elif act == '7':
        _rename_track_in_videos(files, ffmpeg, ffprobe)
    elif act == '8':
        for f in files:
            info = _probe_all(f, ffprobe, ffmpeg)
            auds = info.get('audio') or []
            subs = info.get('subtitles') or []
            print(f'\n{BOLD}{f}{RESET} ({_fmt_time(info.get("duration") or 0)}):')
            if not auds:
                print(f'  {YELLOW}Нет аудиодорожек{RESET}')
            for a in auds:
                print(f'  • {_format_audio_desc(a)}')
            if subs:
                for s in subs:
                    print(f'  • {_format_subtitle_desc(s)}')


def _remove_voices_from_videos(files, ffmpeg, ffprobe):
    """3. Удалить выбранные озвучки из видео (без перекодировки и без faststart)."""
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    auds = info.get('audio') or []
    if not auds:
        print(f'{YELLOW}В файле {first} нет аудиодорожек.{RESET}')
        return

    print(f'\n{CYAN}{BOLD}─── Удаление озвучек из видео (анализ по {first}) ───{RESET}')
    for a in auds:
        print(f'  {GREEN}{a["a_idx"] + 1}{RESET}. {_format_audio_desc(a)}')

    raw = input(
        f'\n{BOLD}Какие дорожки УДАЛИТЬ? (например 2 или 1,3 или 2-4): {RESET}'
    ).strip()
    to_remove = set(_parse_multi_indices(raw, len(auds)))
    if not to_remove:
        print(f'{YELLOW}Ничего не выбрано.{RESET}')
        return

    remaining_preview = [a for a in auds if a['a_idx'] not in to_remove]
    print(f'\n{BOLD}Будут удалены дорожки:{RESET} {", ".join(f"#{i + 1}" for i in sorted(to_remove))}')
    if remaining_preview:
        print(f'{BOLD}Останутся дорожки:{RESET}')
        for a in remaining_preview:
            print(f'  {GREEN}•{RESET} {_format_audio_desc(a)}')
    else:
        print(f'{YELLOW}[!] Внимание: будут удалены ВСЕ аудиодорожки (видео останется без звука)!{RESET}')

    confirm = input(f'\n{BOLD}Применить к {len(files)} файл(ам)? [y/N]: {RESET}').strip().lower()
    if confirm not in ('y', 'yes', 'д', 'да'):
        print(f'{YELLOW}Отмена.{RESET}')
        return

    ok_count = 0
    for f in files:
        f_info = _probe_all(f, ffprobe, ffmpeg)
        f_auds = f_info.get('audio') or []
        keep_indices = [a['a_idx'] for a in f_auds if a['a_idx'] not in to_remove]

        stem, ext = os.path.splitext(f)
        tmp_out = f'{stem}.audio_del_tmp{ext}'

        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-i', f, '-map', '0:v:0']
        if keep_indices:
            for k_idx in keep_indices:
                cmd += ['-map', f'0:a:{k_idx}']
        else:
            cmd += ['-an']
        cmd += ['-map', '0:s?', '-map_metadata', '0', '-c', 'copy', tmp_out]

        print(f'{CYAN}Обработка:{RESET} {f}...')
        r = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        if r.returncode == 0 and os.path.exists(tmp_out):
            try:
                os.replace(tmp_out, f)
                ok_count += 1
                print(f'  {GREEN}✓ Готово: {f}{RESET}')
            except OSError as e:
                print(f'  {RED}✗ Ошибка перезаписи {f}: {e}{RESET}')
        else:
            err_tail = (r.stderr or '')[-300:].replace('\n', ' ')
            print(f'  {RED}✗ Ошибка ffmpeg ({f}): {err_tail}{RESET}')
            try:
                if os.path.exists(tmp_out):
                    os.remove(tmp_out)
            except OSError:
                pass

    print(f'\n{GREEN}{BOLD}Готово! Обработано файлов: {ok_count}/{len(files)}{RESET}')
    try:
        _probe_all_cached.cache_clear()
    except Exception:
        pass


def _rename_voice_in_videos(files, ffmpeg, ffprobe):
    """4. Переименовать одну определённую озвучку (без перекодировки и без faststart)."""
    first = files[0]
    info = _probe_all(first, ffprobe, ffmpeg)
    auds = info.get('audio') or []
    if not auds:
        print(f'{YELLOW}В файле {first} нет аудиодорожек.{RESET}')
        return

    print(f'\n{CYAN}{BOLD}─── Переименование озвучки (анализ по {first}) ───{RESET}')
    for a in auds:
        print(f'  {GREEN}{a["a_idx"] + 1}{RESET}. {_format_audio_desc(a)}')

    if len(auds) == 1:
        target_idx = 0
        print(f'\n{DIM}Выбрана единственная дорожка #1.{RESET}')
    else:
        raw = input(f'\n{BOLD}Какую дорожку переименовать? [1-{len(auds)}]: {RESET}').strip()
        if not (raw.isdigit() and 1 <= int(raw) <= len(auds)):
            print(f'{RED}Неверный номер дорожки.{RESET}')
            return
        target_idx = int(raw) - 1

    cur_title = auds[target_idx].get('title') or ''
    cur_lang = auds[target_idx].get('lang') or 'und'

    new_title = input(
        f'{BOLD}Новое название для дорожки #{target_idx + 1} (текущее: "{cur_title}"): {RESET}'
    ).strip()
    new_lang = input(
        f'{BOLD}Код языка 3 буквы [Enter = оставить "{cur_lang}", например rus/jpn/eng]: {RESET}'
    ).strip().lower()

    mkvpropedit = get_tool_path('mkvpropedit.exe' if os.name == 'nt' else 'mkvpropedit')
    have_mkvpropedit = _have(mkvpropedit)

    ok_count = 0
    for f in files:
        stem, ext = os.path.splitext(f)

        # Если это MKV и установлен mkvpropedit — меняем заголовок мгновенно на месте без перезаписи файла
        if ext.lower() == '.mkv' and have_mkvpropedit:
            cmd = [mkvpropedit, f, '--edit', f'track:a{target_idx + 1}', '--set', f'name={new_title}']
            if new_lang:
                cmd += ['--set', f'language={new_lang}']
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
            if r.returncode == 0:
                ok_count += 1
                print(f'  {GREEN}✓ Мгновенно обновлено (mkvpropedit): {f}{RESET}')
                continue

        # Иначе быстрый ремукс через ffmpeg -c copy (без faststart)
        tmp_out = f'{stem}.audio_ren_tmp{ext}'
        cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-i', f, '-map', '0', '-map_metadata', '0', '-c', 'copy',
               f'-metadata:s:a:{target_idx}', f'title={new_title}']
        if new_lang:
            cmd += [f'-metadata:s:a:{target_idx}', f'language={new_lang}']
        cmd.append(tmp_out)

        r = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
        if r.returncode == 0 and os.path.exists(tmp_out):
            try:
                os.replace(tmp_out, f)
                ok_count += 1
                print(f'  {GREEN}✓ Обновлено: {f}{RESET}')
            except OSError as e:
                print(f'  {RED}✗ Ошибка замены {f}: {e}{RESET}')
        else:
            err_tail = (r.stderr or '')[-300:].replace('\n', ' ')
            print(f'  {RED}✗ Ошибка ffmpeg ({f}): {err_tail}{RESET}')
            try:
                if os.path.exists(tmp_out):
                    os.remove(tmp_out)
            except OSError:
                pass

    print(f'\n{GREEN}{BOLD}Готово! Переименовано в {ok_count}/{len(files)} файл(ах).{RESET}')
    try:
        _probe_all_cached.cache_clear()
    except Exception:
        pass



# ============================================================================
#  ГЛАВНОЕ МЕНЮ
# ============================================================================
def settings_menu():
    ffmpeg = get_tool_path('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    while True:
        cfg = load_config()
        enc, hw = detect_best_hevc_encoder(ffmpeg) if _have(ffmpeg) else ('N/A', [])
        print(f'\n{CYAN}{BOLD}═══ НАСТРОЙКИ И УВЕДОМЛЕНИЯ ═══{RESET}')
        print(f' {GREEN}1.{RESET} Канал Telegram:         {BOLD}{cfg.get("channel", "N/A")}{RESET}')
        print(f' {GREEN}2.{RESET} TDLib API ID:           {BOLD}{cfg.get("api_id", "N/A")}{RESET}')
        print(f' {GREEN}3.{RESET} TDLib API Hash:         {BOLD}{str(cfg.get("api_hash", "N/A"))[:8]}...{RESET}')
        print(f' {GREEN}4.{RESET} Bot Token уведомлений:  {BOLD}{"Задан (" + str(cfg.get("notify_bot_token"))[:10] + "...)" if cfg.get("notify_bot_token") else "Не настроен"}{RESET}')
        print(f' {GREEN}5.{RESET} Chat ID для уведомлений:{BOLD}{cfg.get("notify_chat_id") or "Не указан"}{RESET}')
        print(f' {GREEN}6.{RESET} Отправить тест уведомления в Telegram')
        print(f' {DIM}• Определён энкодер:      {enc} ({"адаптер: " + ", ".join(hw) if hw else "CPU/SW"}){RESET}')
        print(f' {RED}0.{RESET} Назад в главное меню')
        print(f'{CYAN}──────────────────────────────────────────────{RESET}')
        c = input(f'{BOLD}Пункт [0-6]: {RESET}').strip()
        if c in ('0', ''):
            break
        elif c == '1':
            ch = input(f'{BOLD}Новый канал/чат (например -100... или @channel): {RESET}').strip()
            if ch:
                cfg['channel'] = ch
                save_config(cfg)
                print(f'{GREEN}Сохранено!{RESET}')
        elif c == '2':
            aid = input(f'{BOLD}Новый Telegram API ID: {RESET}').strip()
            if aid.isdigit():
                cfg['api_id'] = int(aid)
                save_config(cfg)
                print(f'{GREEN}Сохранено!{RESET}')
        elif c == '3':
            ah = input(f'{BOLD}Новый Telegram API Hash: {RESET}').strip()
            if ah:
                cfg['api_hash'] = ah
                save_config(cfg)
                print(f'{GREEN}Сохранено!{RESET}')
        elif c == '4':
            tok = input(f'{BOLD}Telegram Bot Token (от @BotFather): {RESET}').strip()
            if tok:
                cfg['notify_bot_token'] = tok
                save_config(cfg)
                print(f'{GREEN}Сохранено!{RESET}')
        elif c == '5':
            cid = input(f'{BOLD}Telegram Chat ID (ваш ID от @userinfobot): {RESET}').strip()
            if cid:
                cfg['notify_chat_id'] = cid
                save_config(cfg)
                print(f'{GREEN}Сохранено!{RESET}')
        elif c == '6':
            if not cfg.get("notify_bot_token") or not cfg.get("notify_chat_id"):
                print(f'{RED}Сначала настройте Bot Token (п.4) и Chat ID (п.5)!{RESET}')
            else:
                print(f'{CYAN}Отправка теста...{RESET}')
                send_telegram_notification("🔔 <b>Anime Toolkit</b>\nТестовое уведомление доставлено успешно!")
                print(f'{GREEN}Готово, проверьте сообщения в Telegram!{RESET}')


def main():
    script_dir = _get_script_dir()
    os.chdir(script_dir)

    if os.name == 'nt':
        os.system('chcp 65001 >nul')

    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        print_banner()

        print(f' {GREEN}1.{RESET} {BOLD}Telegram Uploader{RESET}   {DIM}↳ Загрузка в канал через TDLib (авто-посты, превью, теги){RESET}')
        print(f' {GREEN}2.{RESET} {BOLD}Remux в MP4{RESET}         {DIM}↳ Быстрая переупаковка в MP4 + выбор звука + faststart{RESET}')
        print(f' {GREEN}3.{RESET} {BOLD}Smart Rename{RESET}        {DIM}↳ Привести серии к стандарту [01]_[01]_ID через Shikimori{RESET}')
        print(f' {GREEN}4.{RESET} {BOLD}Нарезка (Split){RESET}     {DIM}↳ Разбить видео на части (под лимиты Telegram){RESET}')
        print(f' {GREEN}5.{RESET} {BOLD}Склейка (Merge){RESET}     {DIM}↳ Бесшовное объединение нескольких файлов без потерь{RESET}')
        print(f' {GREEN}6.{RESET} {BOLD}BDRemux Compress{RESET}   {DIM}↳ Аппаратное сжатие HEVC (CQ=16) тяжелых файлов ≥ 4 ГБ{RESET}')
        print(f' {GREEN}7.{RESET} {BOLD}Озвучки и субтитры{RESET} {DIM}↳ Вшивка/извлечение/удаление звука и сабов (Soft/Hardsub){RESET}')
        print(f' {GREEN}8.{RESET} {BOLD}Настройки{RESET}          {DIM}↳ Канал, Telegram API, Бот для уведомлений{RESET}')
        print(f' {RED}0.{RESET} Выход')
        print(f'{CYAN}═════════════════════════════════════════════════════════════════════{RESET}')

        choice = input(f'{BOLD}Действие [0-8]: {RESET}').strip()
        try:
            if choice == '1':
                telegram_uploader()
            elif choice == '2':
                remux_mp4()
            elif choice == '3':
                smart_rename()
            elif choice == '4':
                split_video()
            elif choice == '5':
                merge_videos()
            elif choice == '6':
                compress_bdremux()
            elif choice == '7':
                audio_voice_manager()
            elif choice == '8':
                settings_menu()
            elif choice == '0':
                print(f'\n{YELLOW}До свидания!{RESET}')
                return
        except KeyboardInterrupt:
            print(f'\n{YELLOW}Прервано пользователем.{RESET}')
        except Exception as e:
            print(f'\n{RED}Ошибка: {type(e).__name__}: {e}{RESET}')
        _pause()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if '--__anime_prep_worker' in sys.argv:
        _prep_worker_main()
    else:
        try:
            main()
        except KeyboardInterrupt:
            print(f'\n{YELLOW}Остановлено.{RESET}')