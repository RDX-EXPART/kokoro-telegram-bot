#!/usr/bin/env python3
import os
import sys
import re
import time
import json
import asyncio
import platform
import subprocess
import logging
import hashlib
import inspect
import math
from typing import Optional, List, AsyncGenerator, Union, Awaitable, Tuple, BinaryIO

# Configure logging (silence telethon noise, keep console clean)
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("PrimeXMerge")
logger.setLevel(logging.INFO)
logging.getLogger("telethon").setLevel(logging.WARNING)

from telethon import TelegramClient, events, Button, utils, helpers
from telethon.errors import MessageNotModifiedError
from telethon.crypto import AuthKey
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import GetFileRequest, SaveFilePartRequest, SaveBigFilePartRequest
from telethon.tl.types import (Document, InputFileLocation, InputDocumentFileLocation,
                               InputPeerPhotoFileLocation, InputPhotoFileLocation, TypeInputFile,
                               InputFileBig, InputFile)

# ==================== CONFIGURATION ====================
API_ID = 26107399  # ENTER YOUR API_ID (Integer)
API_HASH = "e10525d8ad0189f8bf7a82a32f538d12"
BOT_TOKEN = "8502096787:AAE-QTuXIZqjVvbD9dVlBx_4lBeQMrABcoM"
WATERMARK_VIDEO_PATH = "ENTER_YOUR_WATERMARK_VIDEO_PATH"  # e.g., "/absolute/path/to/promo.mkv"
DOWNLOADS_DIR = "ENTER_YOUR_DOWNLOADS_DIRECTORY_PATH"  # e.g., "/absolute/path/to/downloads"
# =======================================================

# Ensure download directory exists
if DOWNLOADS_DIR and not DOWNLOADS_DIR.startswith("ENTER_"):
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Initialize Telethon Client
client = TelegramClient('primex_merge_session', API_ID, API_HASH)

async def safe_edit(msg, text, *args, **kwargs):
    if not msg:
        return None
    try:
        return await msg.edit(text, *args, **kwargs)
    except MessageNotModifiedError:
        return msg
    except Exception as e:
        logger.warning(f"Failed to edit message safely: {e}")
        return msg

def escape_ffmpeg_concat_path(path):
    # Escape path for FFmpeg concat demuxer
    escaped = path.replace("\\", "\\\\").replace("'", "'\\''")
    return f"'{escaped}'"

def extract_duration_from_meta(meta):
    if not isinstance(meta, dict):
        return 0.0
    fmt = meta.get("format", {})
    if "duration" in fmt and fmt["duration"]:
        try:
            val = float(fmt["duration"])
            if val > 0:
                return val
        except (ValueError, TypeError):
            pass
    for s in meta.get("streams", []):
        if "duration" in s and s["duration"]:
            try:
                val = float(s["duration"])
                if val > 0:
                    return val
            except (ValueError, TypeError):
                pass
        tags = s.get("tags", {})
        for k, v in tags.items():
            if k.lower() == "duration":
                try:
                    val = float(v)
                    if val > 0:
                        return val
                except (ValueError, TypeError):
                    pass
    return 0.0

async def download_direct_link(url, dest_path, progress_cb=None):
    headers = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    filename = None
    
    # Get Content-Length and Content-Disposition using curl
    cmd_headers = ["curl", "-s", "-I", "-L", "-H", f"User-Agent: {headers}", url]
    total_size = 0
    try:
        proc = await asyncio.create_subprocess_exec(*cmd_headers, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        stdout_str = stdout.decode('utf-8', errors='ignore')
        for line in stdout_str.split("\n"):
            line_lower = line.lower()
            if line_lower.startswith("content-length:"):
                try:
                    total_size = int(line.split(":")[1].strip())
                except Exception:
                    pass
            elif "content-disposition" in line_lower:
                match = re.search(r'filename=["\']?([^"\';\n]+)["\']?', line, re.IGNORECASE)
                if match:
                    filename = match.group(1).strip()
    except Exception as e:
        logger.warning(f"Failed to probe content length or headers: {e}")
        
    if not filename:
        from urllib.parse import urlparse, unquote
        try:
            parsed = urlparse(url)
            path = unquote(parsed.path)
            base = os.path.basename(path)
            if base and "." in base:
                filename = base
        except Exception:
            pass

    cmd_download = ["curl", "-s", "-L", "-H", f"User-Agent: {headers}", "-o", dest_path, url]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd_download)
        while proc.returncode is None:
            await asyncio.sleep(1)
            if os.path.exists(dest_path):
                downloaded = os.path.getsize(dest_path)
                if progress_cb:
                    await progress_cb(downloaded, total_size or downloaded)
        
        await proc.wait()
        if proc.returncode != 0:
            logger.error(f"curl download failed with exit code {proc.returncode}")
            return False, filename
            
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            with open(dest_path, "rb") as f:
                head = f.read(100).lower()
                if b"<html" in head or b"<!doctype" in head:
                    logger.error(f"Downloaded file is HTML (error page), not a video.")
                    try: os.remove(dest_path)
                    except Exception: pass
                    return False, filename
            if progress_cb and total_size:
                await progress_cb(total_size, total_size)
            return True, filename
        return False, filename
    except Exception as err:
        logger.error(f"Error during curl download: {err}")
        if os.path.exists(dest_path):
            try: os.remove(dest_path)
            except Exception: pass
        return False, filename

async def run_ffmpeg_with_progress(cmd, total_duration, status_msg, task_name, prefix=""):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
    last_update = [0.0]
    stderr_lines = []
    
    async def log_reader(stream):
        buffer = b""
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer or b"\r" in buffer:
                idx_n = buffer.find(b"\n")
                idx_r = buffer.find(b"\r")
                if idx_n != -1 and idx_r != -1:
                    idx = min(idx_n, idx_r)
                elif idx_n != -1:
                    idx = idx_n
                else:
                    idx = idx_r
                
                line = buffer[:idx].decode('utf-8', errors='ignore')
                buffer = buffer[idx+1:]
                stderr_lines.append(line)
                
                match = time_pattern.search(line)
                if match and total_duration > 0:
                    hours, minutes, seconds = map(int, match.groups()[:3])
                    curr_seconds = hours * 3600 + minutes * 60 + seconds
                    pct = min((curr_seconds / total_duration) * 100, 100.0)
                    
                    now = time.time()
                    if now - last_update[0] >= 3:
                        last_update[0] = now
                        bar_len = 15
                        filled = int(bar_len * pct // 100)
                        bar = "█" * filled + "░" * (bar_len - filled)
                        await safe_edit(
                            status_msg,
                            f"{prefix}"
                            f"⚡ **{task_name}...**\n"
                            f"`[{bar}] {pct:.1f}%`"
                        )
        if buffer:
            line = buffer.decode('utf-8', errors='ignore')
            stderr_lines.append(line)
            match = time_pattern.search(line)
            if match and total_duration > 0:
                hours, minutes, seconds = map(int, match.groups()[:3])
                curr_seconds = hours * 3600 + minutes * 60 + seconds
                pct = min((curr_seconds / total_duration) * 100, 100.0)
                bar_len = 15
                filled = int(bar_len * pct // 100)
                bar = "█" * filled + "░" * (bar_len - filled)
                await safe_edit(
                    status_msg,
                    f"{prefix}"
                    f"⚡ **{task_name}...**\n"
                    f"`[{bar}] {pct:.1f}%`"
                )
    reader_task = asyncio.create_task(log_reader(proc.stderr))
    await proc.wait()
    await reader_task
    if proc.returncode != 0:
        err_msg = "\n".join(stderr_lines[-20:])
        logger.error(f"FFmpeg failed. Last 20 lines of stderr:\n{err_msg}")
        raise RuntimeError(f"FFmpeg command failed with exit code {proc.returncode}. Error: {err_msg}")

class DownloadSender:
    def __init__(self, client, sender, file, offset, limit, stride, count):
        self.sender = sender
        self.client = client
        self.request = GetFileRequest(file, offset=offset, limit=limit)
        self.stride = stride
        self.remaining = count

    async def next(self):
        if not self.remaining:
            return None
        result = await self.client._call(self.sender, self.request)
        self.remaining -= 1
        self.request.offset += self.stride
        return result.bytes

    def disconnect(self):
        return self.sender.disconnect()

class UploadSender:
    def __init__(self, client, sender, file_id, part_count, big, index, stride, loop):
        self.client = client
        self.sender = sender
        self.part_count = part_count
        if big:
            self.request = SaveBigFilePartRequest(file_id, index, part_count, b"")
        else:
            self.request = SaveFilePartRequest(file_id, index, b"")
        self.stride = stride
        self.previous = None
        self.loop = loop

    async def next(self, data):
        if self.previous:
            await self.previous
        self.previous = self.loop.create_task(self._next(data))

    async def _next(self, data):
        self.request.bytes = data
        await self.client._call(self.sender, self.request)
        self.request.file_part += self.stride

    async def disconnect(self):
        if self.previous:
            await self.previous
        return await self.sender.disconnect()

class ParallelTransferrer:
    def __init__(self, client, dc_id=None):
        self.client = client
        self.loop = self.client.loop
        self.dc_id = dc_id or self.client.session.dc_id
        self.auth_key = (None if dc_id and self.client.session.dc_id != dc_id
                         else self.client.session.auth_key)
        self.senders = None
        self.upload_ticker = 0

    async def _cleanup(self):
        await asyncio.gather(*[sender.disconnect() for sender in self.senders])
        self.senders = None

    @staticmethod
    def _get_connection_count(file_size, max_count=24):
        if file_size < 10 * 1024 * 1024:
            return 4
        return max_count

    async def _init_upload(self, connections, file_id, part_count, big):
        self.senders = [
            await self._create_upload_sender(file_id, part_count, big, 0, connections),
            *await asyncio.gather(
                *[self._create_upload_sender(file_id, part_count, big, i, connections)
                  for i in range(1, connections)])
        ]

    async def _create_upload_sender(self, file_id, part_count, big, index, stride):
        return UploadSender(self.client, await self._create_sender(), file_id, part_count, big, index, stride,
                            loop=self.loop)

    async def _create_sender(self):
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log, auto_reconnect=False)
        await sender.connect(self.client._connection(dc.ip_address, dc.port, dc.id,
                                                     loggers=self.client._log,
                                                     proxy=self.client._proxy))
        if not self.auth_key:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            self.client._init_request.query = ImportAuthorizationRequest(id=auth.id,
                                                                         bytes=auth.bytes)
            req = InvokeWithLayerRequest(LAYER, self.client._init_request)
            await sender.send(req)
            self.auth_key = sender.auth_key
        return sender

    async def init_upload(self, file_id, file_size, part_size_kb=None, connection_count=None):
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = (part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024
        part_count = (file_size + part_size - 1) // part_size
        is_large = file_size > 10 * 1024 * 1024
        await self._init_upload(connection_count, file_id, part_count, is_large)
        return part_size, part_count, is_large

    async def upload(self, part):
        await self.senders[self.upload_ticker].next(part)
        self.upload_ticker = (self.upload_ticker + 1) % len(self.senders)

    async def finish_upload(self):
        await self._cleanup()

def stream_file(file_to_stream, chunk_size=128 * 1024):
    while True:
        data_read = file_to_stream.read(chunk_size)
        if not data_read:
            break
        yield data_read

async def _internal_transfer_to_telegram(client, response, progress_callback):
    file_id = helpers.generate_random_long()
    file_size = os.path.getsize(response.name)
    hash_md5 = hashlib.md5()
    uploader = ParallelTransferrer(client)
    part_size, part_count, is_large = await uploader.init_upload(file_id, file_size)
    buffer = bytearray()
    for data in stream_file(response):
        if progress_callback:
            r = progress_callback(response.tell(), file_size)
            if inspect.isawaitable(r):
                await r
        if not is_large:
            hash_md5.update(data)
        if len(buffer) == 0 and len(data) == part_size:
            await uploader.upload(data)
            continue
        new_len = len(buffer) + len(data)
        if new_len >= part_size:
            cutoff = part_size - len(buffer)
            buffer.extend(data[:cutoff])
            await uploader.upload(bytes(buffer))
            buffer.clear()
            buffer.extend(data[cutoff:])
        else:
            buffer.extend(data)
    if len(buffer) > 0:
        await uploader.upload(bytes(buffer))
    await uploader.finish_upload()
    if is_large:
        return InputFileBig(file_id, part_count, os.path.basename(response.name)), file_size
    else:
        return InputFile(file_id, part_count, os.path.basename(response.name), hash_md5.hexdigest()), file_size

async def inline_upload_file(client, file, progress_callback=None):
    res = (await _internal_transfer_to_telegram(client, file, progress_callback))[0]
    return res

async def fast_upload_file(client, file_path, progress_callback=None):
    retries = 3
    for attempt in range(retries):
        try:
            if not client.is_connected():
                logger.info("Client disconnected. Reconnecting...")
                await client.connect()
            with open(file_path, 'rb') as f:
                return await inline_upload_file(client, f, progress_callback=progress_callback)
        except Exception as e:
            logger.warning(f"Parallel upload attempt {attempt + 1} failed: {e}. Retrying in 3 seconds...")
            if attempt == retries - 1:
                logger.warning("All parallel upload attempts failed. Falling back to native Telethon upload...")
                if not client.is_connected():
                    await client.connect()
                return await client.upload_file(file_path, progress_callback=progress_callback)
            await asyncio.sleep(3)

async def generate_thumbnail(video_path, thumb_path):
    cmd = [
        "ffmpeg", "-y", "-i", video_path, 
        "-ss", "00:00:02", "-vframes", "1", 
        "-vf", "scale=320:-1", 
        thumb_path
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as e:
        logger.error(f"Error generating thumbnail: {e}")
    return None

@client.on(events.NewMessage)
async def merge_handler(event):
    if not event.text:
        return
    if event.text.strip().lower().startswith('/start'):
        return
        
    # Find all URLs in message
    urls = re.findall(r'(https?://[^\s]+)', event.text)
    if not urls:
        return
        
    url = urls[0]
    if "](" in url:
        url = url.split("](")[0]
    url = url.strip("[]()<>")
    
    if "pixeldrain.com/u/" in url:
        url = url.replace("pixeldrain.com/u/", "pixeldrain.com/api/file/")
    thumb_url = None
    # Check if -t is present in the text to extract thumbnail URL
    if "-t" in event.text:
        thumb_match = re.search(r'-t\s+(https?://[^\s]+)', event.text)
        if thumb_match:
            t_url = thumb_match.group(1).strip()
            if "](" in t_url:
                t_url = t_url.split("](")[0]
            thumb_url = t_url.strip("[]()<>")
            
    custom_name = None
    if "-n" in event.text:
        text_parts = event.text.split("-n")
        if len(text_parts) > 1:
            name_part = text_parts[1].strip()
            if "-t" in name_part:
                name_part = name_part.split("-t")[0].strip()
            if name_part:
                custom_name = name_part

    sender = await event.get_sender()
    sender_id = event.sender_id
    
    print(f"\n[+] Received Msg (Link): {url}")
    
    # Check if watermark video path is configured and exists
    if not os.path.exists(WATERMARK_VIDEO_PATH):
        await event.respond(f"❌ **Watermark/Promo video not found at path:** `{WATERMARK_VIDEO_PATH}`\nPlease set the correct path at the top of the script.")
        return
        
    # Probe size and filename using curl headers first
    headers_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    cmd_headers = ["curl", "-s", "-I", "-L", "-H", f"User-Agent: {headers_agent}", url]
    total_size = 0
    download_filename = "video.mp4"
    try:
        proc = await asyncio.create_subprocess_exec(*cmd_headers, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        stdout_str = stdout.decode('utf-8', errors='ignore')
        for line in stdout_str.split("\n"):
            line_lower = line.lower()
            if line_lower.startswith("content-length:"):
                try: total_size = int(line.split(":")[1].strip())
                except Exception: pass
            elif "content-disposition" in line_lower:
                match = re.search(r'filename=["\']?([^"\';\n]+)["\']?', line, re.IGNORECASE)
                if match:
                    download_filename = match.group(1).strip()
    except Exception as e:
        logger.warning(f"Failed to probe headers: {e}")
        
    from urllib.parse import urlparse, parse_qs, unquote
    if download_filename == "video.mp4":
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            if 'response-content-disposition' in qs:
                cd_val = qs['response-content-disposition'][0]
                match = re.search(r'filename=["\']?([^"\';\n]+)["\']?', cd_val, re.IGNORECASE)
                if match:
                    download_filename = match.group(1).strip()
            elif 'filename' in qs:
                download_filename = qs['filename'][0].strip()
            else:
                path = unquote(parsed.path)
                base = os.path.basename(path)
                if base and "." in base:
                    download_filename = base
        except Exception:
            pass

    if custom_name:
        _, custom_ext = os.path.splitext(custom_name)
        if custom_ext.lower() in [".mp4", ".mkv", ".avi", ".webm", ".mov"]:
            download_filename = custom_name
        else:
            _, orig_ext = os.path.splitext(download_filename)
            if not orig_ext or (orig_ext.lower() == ".mp4" and download_filename == "video.mp4"):
                orig_ext = ".mp4"
            download_filename = f"{custom_name}{orig_ext}"

    ext = ".mp4"
    if download_filename and "." in download_filename:
        _, file_ext = os.path.splitext(download_filename)
        if file_ext.lower() in [".mp4", ".mkv", ".avi", ".webm", ".mov"]:
            ext = file_ext.lower()

    size_mb = total_size / (1024 * 1024) if total_size else 0
    original_size_str = f"{size_mb:.2f} MB" if size_mb else "Unknown"
    
    print(f"[+] Origin Video Size: {original_size_str}")
    print(f"[+] File Name: {download_filename}")

    status_text = (
        f"📥 **Link:** {url}\n"
        f"📦 **Size:** {original_size_str}\n"
        f"⏱ **Duration:** --:--:--\n"
        f"🎥 **File Name:** `{download_filename}`\n\n"
        f"⏳ **Downloading main video...**"
    )
    status_msg = await event.respond(status_text)
    
    main_path = os.path.join(DOWNLOADS_DIR, f"main_{sender_id}{ext}")
    part1_path = os.path.join(DOWNLOADS_DIR, f"part1_{sender_id}{ext}")
    part2_path = os.path.join(DOWNLOADS_DIR, f"part2_{sender_id}{ext}")
    matched_custom_path = os.path.join(DOWNLOADS_DIR, f"matched_custom_{sender_id}{ext}")
    final_output_path = os.path.join(DOWNLOADS_DIR, f"merged_output_{sender_id}{ext}")
    list_file_path = os.path.join(DOWNLOADS_DIR, f"concat_list_{sender_id}.txt")
    thumbnail_path = os.path.join(DOWNLOADS_DIR, f"thumb_{sender_id}.jpg")
    movie_title = download_filename
    
    # Clean up old files
    for p in [main_path, part1_path, part2_path, matched_custom_path, final_output_path, list_file_path, thumbnail_path]:
        if os.path.exists(p):
            try: os.remove(p)
            except Exception: pass
            
    try:
        last_edit = [0]
        start_time = time.time()
        
        async def progress_cb(current, total):
            now = time.time()
            if now - last_edit[0] >= 3:
                last_edit[0] = now
                pct = (current / total) * 100 if total else 0
                elapsed = now - start_time
                speed = current / elapsed if elapsed else 0
                remaining = total - current
                eta_sec = remaining / speed if speed else 0
                if eta_sec > 0:
                    eta_min = int(eta_sec // 60)
                    eta_s = int(eta_sec % 60)
                    eta_str = f"{eta_min:02d}:{eta_s:02d}"
                else:
                    eta_str = "--:--"
                    
                bar_len = 15
                filled = int(bar_len * pct // 100)
                bar = "█" * filled + "░" * (bar_len - filled)
                
                status_text = (
                    f"📥 **Link:** {url}\n"
                    f"📦 **Size:** {original_size_str}\n"
                    f"⏱ **Duration:** --:--:--\n"
                    f"🎥 **File Name:** `{download_filename}`\n\n"
                    f"⏳ **Downloading main video: {pct:.1f}%**\n"
                    f"`[{bar}]` (ETA: {eta_str})"
                )
                try:
                    await client.edit_message(status_msg, status_text)
                except Exception:
                    pass
                    
        print("[*] Downloading main video...")
        success, dl_name = await download_direct_link(url, main_path, progress_cb)
        if not success or not os.path.exists(main_path) or os.path.getsize(main_path) == 0:
            raise RuntimeError("Failed to download main video from URL.")
            
        # Update original size from downloaded file if it was unknown
        if not total_size or total_size == 0:
            total_size = os.path.getsize(main_path)
            size_mb = total_size / (1024 * 1024)
            original_size_str = f"{size_mb:.2f} MB"
            print(f"[+] Origin Video Size (Updated): {original_size_str}")
            
        print("[*] Probing video properties...")
        cmd_probe = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,bit_rate:stream=width,height,r_frame_rate,codec_name,sample_rate,channels,codec_type,pix_fmt,duration",
            "-of", "json", main_path
        ]
        proc = await asyncio.create_subprocess_exec(*cmd_probe, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        meta = json.loads(stdout.decode('utf-8', errors='ignore'))
        
        duration = extract_duration_from_meta(meta)
        if duration <= 0:
            raise RuntimeError("Could not determine video duration for merging.")
            
        dur_hours = int(duration // 3600)
        dur_minutes = int((duration % 3600) // 60)
        dur_seconds = int(duration % 60)
        duration_str = f"{dur_hours:02d}:{dur_minutes:02d}:{dur_seconds:02d}"
        
        print(f"[+] Duration: {duration_str}")
        
        if not movie_title:
            meta_title = meta.get("format", {}).get("tags", {}).get("title")
            if meta_title:
                movie_title = meta_title
            else:
                movie_title = "Merged Video"
                
        # Keep original format extension in title
        if "format" not in meta or "streams" not in meta or not meta["streams"]:
            raise RuntimeError("The downloaded file is not a valid video file.")
            
        v_stream = [s for s in meta["streams"] if s["codec_type"] == "video"][0]
        a_stream = [s for s in meta["streams"] if s["codec_type"] == "audio"]
        
        width = int(v_stream["width"])
        height = int(v_stream["height"])
        fps_str = v_stream["r_frame_rate"]
        fps = float(fps_str.split('/')[0]) / float(fps_str.split('/')[1]) if '/' in fps_str else float(fps_str)
        pix_fmt = v_stream.get("pix_fmt", "yuv420p")
        
        has_audio = len(a_stream) > 0
        audio_sample_rate = a_stream[0]["sample_rate"] if has_audio else None
        audio_channels = a_stream[0]["channels"] if has_audio else None
        
        # Extract Sample Aspect Ratio (SAR)
        sar = v_stream.get("sample_aspect_ratio", "1:1")
        if not sar or sar == "0:1":
            sar = "1:1"
            
        # Extract video timescale
        video_timebase = v_stream.get("time_base", "1/90000")
        video_timescale = video_timebase.split('/')[-1] if '/' in video_timebase else "90000"
        
        # Extract H.264 profile
        profile = v_stream.get("profile", "High").lower()
        if "high" in profile:
            profile_flag = ["-profile:v", "high", "-level:v", "4.1"]
        elif "main" in profile:
            profile_flag = ["-profile:v", "main", "-level:v", "4.0"]
        elif "baseline" in profile:
            profile_flag = ["-profile:v", "baseline", "-level:v", "3.0"]
        else:
            profile_flag = ["-profile:v", "high", "-level:v", "4.1"]
        
        original_bitrate = meta.get("format", {}).get("bit_rate")
        if not original_bitrate:
            original_bitrate = v_stream.get("bit_rate")
        if not original_bitrate:
            original_bitrate = "1100k"
        else:
            try:
                original_bitrate = f"{int(original_bitrate) // 1000}k"
            except Exception:
                original_bitrate = "1100k"
                
        midpoint = duration / 2.0
        await safe_edit(status_msg, f"🎬 **Main video length: {duration:.1f}s. Splitting at midpoint: {midpoint:.1f}s...**")
        
        split1_cmd = ["ffmpeg", "-y", "-i", main_path, "-t", str(midpoint), "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero", part1_path]
        split2_cmd = ["ffmpeg", "-y", "-i", main_path, "-ss", str(midpoint), "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero", part2_path]
        
        p1 = await asyncio.create_subprocess_exec(*split1_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p1.wait()
        p2 = await asyncio.create_subprocess_exec(*split2_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p2.wait()
        
        await safe_edit(status_msg, "⏳ **Fitting custom video to main video specifications...**")
        vcodec = "h264_videotoolbox" if platform.system() == "Darwin" else "libx264"
        
        # Probe part1 properties to match custom video perfectly
        p1_timescale = "16000"
        p1_sar = "1:1"
        try:
            cmd_p1_probe = [
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,time_base,sample_aspect_ratio",
                "-of", "json", part1_path
            ]
            proc_p1 = await asyncio.create_subprocess_exec(*cmd_p1_probe, stdout=asyncio.subprocess.PIPE)
            stdout_p1, _ = await proc_p1.communicate()
            meta_p1 = json.loads(stdout_p1.decode('utf-8'))
            v_stream_p1 = [s for s in meta_p1["streams"] if s["codec_type"] == "video"][0]
            
            p1_timebase = v_stream_p1.get("time_base", "1/16000")
            p1_timescale = p1_timebase.split('/')[-1] if '/' in p1_timebase else "16000"
            
            p1_sar = v_stream_p1.get("sample_aspect_ratio", "1:1")
            if not p1_sar or p1_sar == "0:1":
                p1_sar = "1:1"
        except Exception as pe:
            logger.warning(f"Failed to probe part1 properties: {pe}")

        custom_has_audio = False
        try:
            cmd_custom_probe = [
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                "-of", "json", WATERMARK_VIDEO_PATH
            ]
            proc_c = await asyncio.create_subprocess_exec(*cmd_custom_probe, stdout=asyncio.subprocess.PIPE)
            stdout_c, _ = await proc_c.communicate()
            meta_c = json.loads(stdout_c.decode('utf-8'))
            custom_has_audio = any(s["codec_type"] == "audio" for s in meta_c["streams"])
        except Exception:
            pass

        # Build custom video conversion command matching all main audio streams dynamically
        custom_convert_cmd = ["ffmpeg", "-y"]
        custom_convert_cmd.extend(["-i", WATERMARK_VIDEO_PATH])
        
        need_silent_src = (not custom_has_audio) or (len(a_stream) > 1)
        if need_silent_src:
            sr = audio_sample_rate or "48000"
            ch = "stereo" if str(audio_channels) == "2" else "mono"
            custom_convert_cmd.extend(["-f", "lavfi", "-i", f"anullsrc=r={sr}:cl={ch}"])
            
        video_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format={pix_fmt},setsar={p1_sar}"
        custom_convert_cmd.extend(["-filter_complex", f"[0:v]{video_filter}[outv]"])
        custom_convert_cmd.extend(["-map", "[outv]"])
        
        if len(a_stream) > 0:
            for idx, ast in enumerate(a_stream):
                if idx == 0 and custom_has_audio:
                    custom_convert_cmd.extend(["-map", "0:a"])
                else:
                    custom_convert_cmd.extend(["-map", "1:a"])
                    
            custom_convert_cmd.extend(["-c:a", "aac"])
            for idx, ast in enumerate(a_stream):
                sr = ast.get("sample_rate", "48000")
                ch = ast.get("channels", "2")
                custom_convert_cmd.extend([
                    f"-ar:{idx}", str(sr),
                    f"-ac:{idx}", str(ch)
                ])
            if need_silent_src:
                custom_convert_cmd.append("-shortest")
        else:
            custom_convert_cmd.append("-an")
            
        custom_convert_cmd.extend([
            "-r", str(fps), "-c:v", vcodec, "-b:v", original_bitrate
        ])
        custom_convert_cmd.extend(profile_flag)
        custom_convert_cmd.extend([
            "-video_track_timescale", str(p1_timescale),
            "-g", "24"
        ])
        if platform.system() != "Darwin":
            custom_convert_cmd.extend(["-preset", "ultrafast"])
        custom_convert_cmd.append(matched_custom_path)
        
        p_custom = await asyncio.create_subprocess_exec(*custom_convert_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p_custom.wait()
        
        custom_duration = 5.0
        try:
            cmd_c_probe = [
                "ffprobe", "-v", "error", "-show_entries", "stream=duration:format=duration",
                "-of", "json", matched_custom_path
            ]
            proc_c_dur = await asyncio.create_subprocess_exec(*cmd_c_probe, stdout=asyncio.subprocess.PIPE)
            stdout_c_dur, _ = await proc_c_dur.communicate()
            meta_c_dur = json.loads(stdout_c_dur.decode('utf-8'))
            custom_duration = extract_duration_from_meta(meta_c_dur) or 5.0
        except Exception:
            pass

        # Define prefix for status updates
        prefix = (
            f"📥 **Link:** {url}\n"
            f"📦 **Size:** {original_size_str}\n"
            f"⏱ **Duration:** {duration_str}\n"
            f"🎥 **File Name:** `{download_filename}`\n\n"
        )
        
        # Try fast stream copy concat first
        print("[*] Merging video parts...")
        await safe_edit(status_msg, f"{prefix}⚡ **Merging all parts (fast stream copying)...**")
        with open(list_file_path, "w") as f_list:
            f_list.write(f"file {escape_ffmpeg_concat_path(part1_path)}\n")
            f_list.write(f"file {escape_ffmpeg_concat_path(matched_custom_path)}\n")
            f_list.write(f"file {escape_ffmpeg_concat_path(part2_path)}\n")
            
        fast_concat_cmd = [
            "ffmpeg", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0",
            "-i", list_file_path, "-map", "0", "-c", "copy", final_output_path
        ]
        
        try:
            await run_ffmpeg_with_progress(fast_concat_cmd, duration + custom_duration, status_msg, "Merging all parts (fast stream copying)", prefix)
        except Exception as copy_err:
            logger.warning(f"Fast copy merge failed ({copy_err}), falling back to transcoding merge...")
            await safe_edit(status_msg, f"{prefix}⚡ **Codec mismatch detected. Fallback to transcoding merge...**")
            
            if has_audio:
                transcode_concat_cmd = [
                    "ffmpeg", "-y",
                    "-i", part1_path,
                    "-i", matched_custom_path,
                    "-i", part2_path,
                    "-filter_complex", (
                        "[0:v:0]setsar=1,setpts=PTS-STARTPTS[v0]; [0:a:0]asetpts=PTS-STARTPTS[a0]; "
                        "[1:v:0]setsar=1,setpts=PTS-STARTPTS[v1]; [1:a:0]asetpts=PTS-STARTPTS[a1]; "
                        "[2:v:0]setsar=1,setpts=PTS-STARTPTS[v2]; [2:a:0]asetpts=PTS-STARTPTS[a2]; "
                        "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[outv][outa]"
                    ),
                    "-map", "[outv]", "-map", "[outa]",
                    "-c:v", vcodec, "-b:v", original_bitrate, "-c:a", "aac"
                ]
            else:
                transcode_concat_cmd = [
                    "ffmpeg", "-y",
                    "-i", part1_path,
                    "-i", matched_custom_path,
                    "-i", part2_path,
                    "-filter_complex", (
                        "[0:v:0]setsar=1,setpts=PTS-STARTPTS[v0]; "
                        "[1:v:0]setsar=1,setpts=PTS-STARTPTS[v1]; "
                        "[2:v:0]setsar=1,setpts=PTS-STARTPTS[v2]; "
                        "[v0][v1][v2]concat=n=3:v=1:a=0[outv]"
                    ),
                    "-map", "[outv]",
                    "-c:v", vcodec, "-b:v", original_bitrate
                ]
            if platform.system() != "Darwin":
                transcode_concat_cmd.extend(["-preset", "ultrafast"])
            transcode_concat_cmd.append(final_output_path)
            await run_ffmpeg_with_progress(transcode_concat_cmd, duration + custom_duration, status_msg, "Merging all parts (transcoding for smoothness)", prefix)
            
        if not os.path.exists(final_output_path) or os.path.getsize(final_output_path) == 0:
            raise RuntimeError("FFmpeg merge output file was not created or is empty.")
            
        print(f"[*] Uploading merged video ({original_size_str}, {duration_str})...")
        await safe_edit(status_msg, f"{prefix}📤 **Uploading final merged video to Telegram...**")
        
        # Upload to chat
        async def upload_progress_cb(current, total):
            pct = (current / total) * 100 if total else 0
            now = time.time()
            if now - last_edit[0] >= 3:
                last_edit[0] = now
                bar_len = 15
                filled = int(bar_len * pct // 100)
                bar = "█" * filled + "░" * (bar_len - filled)
                try:
                    await client.edit_message(status_msg, f"{prefix}📤 **Uploading merged video: {pct:.1f}%**\n`[{bar}]`")
                except Exception:
                    pass

        # Probe final merged video duration
        final_duration = duration + custom_duration
        try:
            cmd_final_probe = [
                "ffprobe", "-v", "error", "-show_entries", "stream=duration:format=duration",
                "-of", "json", final_output_path
            ]
            proc_f = await asyncio.create_subprocess_exec(*cmd_final_probe, stdout=asyncio.subprocess.PIPE)
            stdout_f, _ = await proc_f.communicate()
            meta_f = json.loads(stdout_f.decode('utf-8'))
            final_duration = extract_duration_from_meta(meta_f) or (duration + custom_duration)
        except Exception:
            pass

        # Generate or download thumbnail
        thumbnail_path = os.path.join(DOWNLOADS_DIR, f"thumb_{sender_id}.jpg")
        if os.path.exists(thumbnail_path):
            try: os.remove(thumbnail_path)
            except Exception: pass
            
        thumb_success = False
        if thumb_url:
            await safe_edit(status_msg, "📥 **Downloading custom thumbnail...**")
            cmd_thumb = ["curl", "-s", "-L", "-o", thumbnail_path, thumb_url]
            proc_thumb = await asyncio.create_subprocess_exec(*cmd_thumb, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc_thumb.wait()
            if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
                thumb_success = True
                
        if not thumb_success:
            await generate_thumbnail(main_path, thumbnail_path)
        
        uploaded_thumb = None
        if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            try:
                uploaded_thumb = await client.upload_file(thumbnail_path)
            except Exception as thumb_up_err:
                logger.warning(f"Failed to pre-upload thumbnail: {thumb_up_err}")

        # Build video attributes for landscape / correct rendering
        from telethon.tl.types import DocumentAttributeVideo
        video_attrs = DocumentAttributeVideo(
            duration=int(duration),
            w=width,
            h=height,
            supports_streaming=True
        )

        uploaded_file = await fast_upload_file(client, final_output_path, progress_callback=upload_progress_cb)
        sent_msg = await client.send_file(
            event.chat_id,
            uploaded_file,
            caption=f"**{movie_title}**",
            attributes=[video_attrs],
            supports_streaming=True,
            thumb=uploaded_thumb
        )
        
        # Format midpoint duration to HH:MM:SS
        m_hours = int(midpoint // 3600)
        m_minutes = int((midpoint % 3600) // 60)
        m_seconds = int(midpoint % 60)
        watermark_time = f"{m_hours:02d}:{m_minutes:02d}:{m_seconds:02d}"
        
        await sent_msg.reply(f"📌 **Watermark / Promo video added at:** `{watermark_time}`")
        await status_msg.delete()
        print("[+] Done!\n")
        
    except Exception as err:
        print(f"[-] Error: {err}")
        logger.exception(f"Merge failed: {err}")
        await safe_edit(status_msg, f"❌ **Merge failed:** `{err}`")
    finally:
        # Clean up temp files
        for p in [main_path, part1_path, part2_path, matched_custom_path, final_output_path, list_file_path, thumbnail_path]:
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass

@client.on(events.NewMessage(pattern=re.compile(r'^/start', re.IGNORECASE)))
async def start_handler(event):
    await event.respond("👋 **Welcome to PrimeX Video Merge Bot!**\n\nJust send/share any direct video download link here. The bot will download the video, insert your watermark video at the center (midpoint), merge them, and send the final video back to you.")

if __name__ == '__main__':
    print("Starting PrimeX Video Merge Bot...")
    client.start(bot_token=BOT_TOKEN)
    print("Bot is running!")
    client.run_until_disconnected()
