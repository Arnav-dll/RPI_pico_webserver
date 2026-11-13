# Raspberry Pi Pico W MicroSD HTTP File Server

An asynchronous, lightweight, and robust HTTP file server for the **Raspberry Pi Pico W** running MicroPython. It turns your Pico W into a secure network-attached storage (NAS) device and web file manager powered by an SPI MicroSD card module.

Featuring a modern, responsive web dashboard with live upload progress metrics, RESTful API endpoints for `curl` automation, Basic HTTP authentication, and low-memory streaming architecture that handles gigabyte-sized files on just 264 KB of microcontroller RAM.

---

## Table of Contents

- [Features](#-features)
- [Hardware Requirements & Pinout](#-hardware-requirements--pinout)
- [Project Structure](#-project-structure)
- [Installation & Setup](#-installation--setup)
  - [1. Format the MicroSD Card](#1-format-the-microsd-card)
  - [2. Flash MicroPython Firmware](#2-flash-micropython-firmware)
  - [3. Configure Credentials](#3-configure-credentials)
  - [4. Deploy Code to Pico W](#4-deploy-code-to-pico-w)
- [Usage Guide](#-usage-guide)
  - [Web UI Dashboard](#web-ui-dashboard)
  - [CLI & cURL Automation](#cli--curl-automation)
- [Technical Architecture](#-technical-architecture)
  - [Asynchronous Server Pipeline](#1-asynchronous-server-pipeline)
  - [Zero-Allocation Memory & Streaming](#2-zero-allocation-memory--streaming)
  - [SPI Bus & FAT32 VFS Layer](#3-spi-bus--fat32-vfs-layer)
  - [Security & Path Traversal Prevention](#4-security--path-traversal-prevention)
  - [NTP Clock Synchronization & Audit Log](#5-ntp-clock-synchronization--audit-log)
- [Configuration Reference](#-configuration-reference)
- [Troubleshooting](#-troubleshooting)

---

## Features

* **Responsive Web Dashboard**: Clean CSS interface that adapts to mobile, tablet, and desktop viewports.
* **Real-Time Upload Progress**: Upload files of any size with dynamic progress bar, upload speed (KB/s), and calculated ETA.
* **Zero-Allocation Streaming**: Streams uploads and downloads in chunked buffers using `memoryview` and pre-allocated byte arrays, preventing MicroPython heap exhaustion and GC stalls.
* **Dual-Mode Endpoints**: Automatically serves HTML for browsers and JSON for programmatic API requests (`curl`, Python, scripts).
* **Basic HTTP Authentication**: All browser and API routes are secured with credentials verified in middleware.
* **Directory Management**: Recursive folder creation (`mkdir`) and recursive non-empty directory deletion (`rmtree`).
* **Audit & Overwrite Logging**: Real-time RTC clock synchronization via NTP; automatically logs file overwrites to `/sd/overwrite_log.txt`.
* **Visual Status Indicators**: Onboard LED pulses during Wi-Fi connection and flashes asynchronously during file transfer I/O.

---

## Hardware Requirements & Pinout

### Required Components
1. **Raspberry Pi Pico W** (or Pico 2 W) with headers.
2. **MicroSD Card Module / SPI Adapter** (ensure it operates at or is level-shifted for 3.3V logic).
3. **MicroSD Card** (FAT32 formatted, Class 10 / A1 recommended).
4. **Jumper Wires** (keep SPI wires < 10 cm for high signal integrity).
5. **Micro-USB Cable** & stable 5V 1A+ power source.

### SPI0 Wiring Diagram

The server uses the hardware **SPI0** peripheral on the RP2040:

| MicroSD Module Pin | Pico W Physical Pin | Pico W GPIO Pin | Function |
| :--- | :--- | :--- | :--- |
| **VCC** | Pin 36 | `3V3(OUT)` | 3.3V Power Supply |
| **GND** | Pin 38 (or any GND) | `GND` | Ground |
| **MISO** (DO) | Pin 21 | `GP16` | SPI0 RX / Master In Slave Out |
| **CS** (SS) | Pin 22 | `GP17` | Chip Select (Software Controlled) |
| **SCK** (CLK) | Pin 24 | `GP18` | SPI0 SCK / Clock |
| **MOSI** (DI) | Pin 25 | `GP19` | SPI0 TX / Master Out Slave In |

> [!IMPORTANT]
> Always supply the SD card module with clean 3.3V. SD cards can draw instantaneous peak currents of up to 100–150 mA during flash write cycles.

---

## Project Structure

```
Pico-w-server/
├── main.py            # Main application entry point, web routes, and UI generator
├── microdot.py        # Microdot asynchronous web framework for MicroPython
├── sdcard.py          # SPI block device driver for SDSC/SDHC/SDXC cards
├── secrets.py         # Network credentials and authentication optimization and URL decoding
└── README.md          # Project documentation and user guide
```

---

## Installation & Setup

### 1. Format the MicroSD Card
The SD card must be formatted as **FAT32**. For optimal read/write performance on MicroPython, format the card with a **32 KB or 64 KB cluster allocation unit size**:

* **Windows (Command Prompt / PowerShell)**:
  ```powershell
  format E: /FS:FAT32 /A:32K /Q
  ```
  *(Replace `E:` with your SD card drive letter)*
* **macOS / Linux**:
  ```bash
  sudo mkfs.vfat -F 32 -s 64 /dev/sdX1
  ```

### 2. Flash MicroPython Firmware
Download and flash the official [MicroPython firmware for Raspberry Pi Pico W](https://micropython.org/download/RPI_PICO_W/) (v1.20 or newer recommended).

### 3. Configure Credentials
Create or edit `secrets.py` in the project root:

```python
# secrets.py
WIFI_SSID = "Your_WiFi_Network_Name"
WIFI_PASSWORD = "Your_WiFi_Password"

AUTH_USER = "admin"
AUTH_PASS = "YourSecurePassword"
```

### 4. Deploy Code to Pico W
Using [Thonny IDE](https://thonny.org/), `mpremote`, or `rshell`, upload the following files to the root of the Pico W's flash filesystem:
- `main.py`
- `microdot.py`
- `sdcard.py`
- `secrets.py`

When using `mpremote`:
```bash
mpremote connect port:COM3 cp secrets.py sdcard.py microdot.py main.py :
```
*(Replace `COM3` with your Pico W serial port, or `/dev/ttyACM0` on Linux)*

---

## Usage Guide

### Boot Sequence
1. Power on the Pico W.
2. The onboard LED will blink while negotiating Wi-Fi connection.
3. Once connected, time is synced via NTP, the SD card is initialized and mounted at `/sd`, and the assigned local IP address will be printed to the REPL serial console:
   ```
   [OK] SD Card mounted at /sd (12 MHz SPI)
   Connecting to Wi-Fi...
   [OK] Connected! Pico W IP Address: http://192.168.1.150
   [OK] Time synced via NTP
   Starting server on http://192.168.1.150:80 ...
   ```

---

### Web UI Dashboard

Open your web browser and navigate to:
```
http://<Pico-W-IP-Address>/
```
Enter your `AUTH_USER` and `AUTH_PASS` when prompted by the browser's Basic Auth dialog.

#### Dashboard Capabilities:
* **Directory Navigation**: Click on any folder row `📁 folder_name/` to enter. Click `📁 .. (Parent Directory)` to navigate upward.
* **File Downloads**: Click **Download** next to any file. The file will stream natively to your browser with full `Content-Length` metadata.
* **File Uploads**: Select a file from the card grid and click **Upload to Current Folder**. Files larger than 64 KB activate an animated progress bar indicating percentage, upload speed in KB/s, and remaining ETA.
* **Folder Creation**: Enter a name in the **Create New Folder** box and click **Create Folder**.
* **Deletion**: Click **Delete** for a file or **Delete Folder** for a directory. Recursive deletion will safely purge all child contents.
* **Audit Log Viewer**: Click **📋 View Overwrite Log** in the upper right header to review timestamps of all overwritten files.

---

### CLI & cURL Automation

Because `main.py` inspects the HTTP `User-Agent` and `Accept` headers, you can interact with the server via `curl` or automated scripts.

All examples use `-u admin:pico123` for Basic HTTP authentication:

#### 1. List Directory Contents (JSON)
```bash
curl -u admin:pico123 http://192.168.1.150/
```
**Example Response:**
```json
{
  "directory": "/",
  "contents": [
    {"name": "documents", "type": "dir", "size": 0, "mtime": 1731535200},
    {"name": "firmware.bin", "type": "file", "size": 245760, "mtime": 1731535250}
  ]
}
```

To list a subfolder:
```bash
curl -u admin:pico123 "http://192.168.1.150/?dir=documents"
```

#### 2. Download a File
```bash
curl -u admin:pico123 -O http://192.168.1.150/download/documents/report.pdf
```

#### 3. Upload a File
Send a binary stream via `PUT`:
```bash
curl -u admin:pico123 --upload-file firmware.bin http://192.168.1.150/upload/firmware.bin
```
Upload into a nested directory (directories are created automatically if they don't exist):
```bash
curl -u admin:pico123 --upload-file photo.jpg http://192.168.1.150/upload/backup/2026/photo.jpg
```

#### 4. Create a Directory
```bash
curl -u admin:pico123 -X POST http://192.168.1.150/mkdir/projects/iot
```

#### 5. Delete a File or Folder
```bash
# Delete a file
curl -u admin:pico123 -X DELETE http://192.168.1.150/delete/documents/old_report.pdf

# Recursively delete a folder
curl -u admin:pico123 -X DELETE http://192.168.1.150/delete/projects/iot
```

#### 6. Inspect Overwrite Logs
```bash
# View complete log
curl -u admin:pico123 http://192.168.1.150/logs

# Check overwrite history of a single target file
curl -u admin:pico123 http://192.168.1.150/check_log/firmware.bin
```

---

## Technical Architecture

### 1. Asynchronous Server Pipeline
The server is built on **Microdot**, a lightweight, Flask-like microframework engineered for MicroPython. 
- Operates under `uasyncio`, running cooperatively inside the single-threaded RP2040 runtime.
- Employs non-blocking network socket polling over lwIP (Lightweight IP stack).
- An asynchronous background task (`blink_led_task`) runs alongside active transfers to provide hardware visual feedback without stalling I/O.

### 2. Zero-Allocation Memory & Streaming

The RP2040 has **264 KB of SRAM**. Storing large file payloads in RAM causes instant `MemoryError` crashes and heap fragmentation.

* **Streaming Uploads (`PUT /upload/`)**:
  - The request payload is streamed directly from the socket in 2048-byte chunks (`request.stream.read()`).
  - `await asyncio.sleep(0)` yields execution time back to the CYW43439 Wi-Fi driver, preventing TCP buffer overruns.
  - Periodic garbage collection (`gc.collect()`) and file syncs maintain heap health.
* **Streaming Downloads (`GET /download/`)**:
  - Employs Python generators (`yield`) with a single pre-allocated `bytearray(4096)`.
  - Reads directly into memory using `f.readinto(buf)` and yields `memoryview(buf)[:size]`.
  - **Zero allocations occur in the loop**, eliminating heap churn and GC pauses during downloads.

### 3. SPI Bus & FAT32 VFS Layer
- **Two-Stage Initialization**:
  1. Handshakes at **1 MHz** using standard SD card SPI mode initialization sequences (`CMD0`, `CMD8`, `CMD55`, `ACMD41`).
  2. Promotes the hardware SPI clock to **12 MHz** (or up to 25 MHz) for high-speed block transfers.
- **MicroPython VFS Integration**:
  - `sdcard.SDCard` implements the block device protocol (`readblocks`, `writeblocks`, `ioctl`).
  - Bound to MicroPython's Virtual File System using `os.VfsFat(sd)` and mounted to `/sd`.
  - Standard Python file primitives (`open()`, `os.stat()`, `os.listdir()`, `os.mkdir()`) operate transparently over the SD card.

### 4. Security & Path Traversal Prevention
- **Authentication**: A global `@app.before_request` hook validates the `Authorization: Basic <base64>` header against pre-computed hashes before invoking any route handler.
- **Directory Traversal Mitigation**: The `sanitize_path()` utility breaks incoming paths into segments, strips null characters and relative parent references (`..`), and anchors all paths under `/sd`. Root SD folder deletion is strictly prevented.

### 5. NTP Clock Synchronization & Audit Log
- At startup, the server contacts pool NTP servers via `ntptime.settime()` to set the onboard hardware Real-Time Clock (RTC).
- File uploads check if a destination file already exists. If an existing file is replaced, an audit entry is appended to `/sd/overwrite_log.txt` with formatted timestamps:
  ```
  [2026-11-13 22:15:02] OVERWRITTEN: /firmware.bin
  ```

---

## Configuration Reference

Key constants and parameters in `main.py`:

| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| `SD_MOUNT_PATH` | `"/sd"` | VFS mount point on the Pico filesystem |
| `LOG_FILE` | `"/sd/overwrite_log.txt"` | Destination for audit logs |
| `PIN_MISO`, `PIN_CS`, `PIN_SCK`, `PIN_MOSI` | `16, 17, 18, 19` | Hardware SPI0 pin mappings |
| `Request.max_content_length` | `2 * 1024 * 1024 * 1024` (2 GB) | Maximum permitted HTTP upload payload |
| `Request.max_body_length` | `16 * 1024` (16 KB) | Threshold above which bodies are streamed |
| `chunk_size` | `2048` | Stream read buffer size for file uploads |
| `SPI baudrate` | `12_000_000` (12 MHz) | Operational clock rate for SPI communications |

> [!TIP]
> For instructions on how to overclock the RP2040 to 250 MHz, boost SPI to 25 MHz, and eliminate driver latency, refer to [**`optimize.md`**](file:///c:/Users/HP/Desktop/codes/Pico-w-server/optimize.md).

---

## Troubleshooting

### 1. "Failed to mount SD Card: no SD card"
* **Wiring**: Verify all 6 jumper wires. Ensure MISO goes to GP16 and MOSI goes to GP19 (do not cross RX/TX).
* **Power**: Check that the module is powered from Pin 36 (`3V3(OUT)`). If your module has an onboard 3.3V LDO regulator that requires 5V, power it from Pin 40 (`VBUS`).
* **Format**: Verify the card is formatted as FAT32, not exFAT or NTFS.

### 2. Wi-Fi Won't Connect (LED Continues to Blink)
* Verify SSID and password in `secrets.py`.
* Ensure your Wi-Fi router broadcasts a **2.4 GHz** network (Pico W does not support 5 GHz Wi-Fi).

### 3. File or Folder Names Show `%20` Instead of Spaces
* **Cause**: Browsers URL-encode spaces as `%20`. Microdot extracts path segments verbatim without automatic decoding.
* **Fix**: Apply the `urldecode` patch detailed in [**`optimize.md`**](file:///c:/Users/HP/Desktop/codes/Pico-w-server/optimize.md#fix-a-update-sanitize_path-in-mainpy) to `sanitize_path()`.

### 4. Upload Fails on Large Files
* Use a high-quality, stable USB power supply. Flash writes cause current spikes that can reset the Pico W if powered by weak USB ports.
* Format the SD card with a 32 KB cluster size to reduce FAT table seek overhead.

---

## License

This project is open-source and free to use under the MIT License.
