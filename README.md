# Termux 기반 인스타그램 포스트 정기 수집 시스템 (Termux-Instagram-Collector)

안드로이드 공기계(주거용 모바일/Wi-Fi IP 환경)를 활용하여 특정 인스타그램 계정의 게시물을 정기적으로 수집하고, Git 기반 무인 배포 및 텔레그램 봇을 통한 원격 세션 관리 체계를 제공하는 시스템입니다.

---

## 📁 프로젝트 구조 (Project Structure)

```text
instagram-scraper/
├── .gitignore
├── .env.example              # 환경 변수 템플릿
├── requirements.txt          # 파이썬 의존성 패키지
├── config.py                 # 환경변수 로더 및 경로 설정
├── db.py                     # SQLite 연결, 테이블 생성 및 데이터 처리 핸들러
├── session_manager.py        # 원자적(Atomic) 세션 저장 및 유효성 검증
├── telegram_notifier.py      # 스크래퍼 비상 알림(세션 만료, 레이트 리밋 등) 모듈
├── scraper.py                # 메인 수집 엔진 (Cron 구동)
├── telegram_bot.py           # 텔레그램 데몬 (상시 구동, 원격 명령 제어)
├── start_services.sh         # Termux 부팅 및 통합 서비스 실행 스크립트
├── stop_services.sh          # 서비스 종료 스크립트
├── data/                     # [Git 제외] SQLite DB 저장 디렉토리
│   └── scraper.db            # SQLite 데이터베이스
├── logs/                     # [Git 제외] 로그 디렉토리
│   ├── cron.log              # 스크래퍼 실행 로그
│   ├── bot.log               # 텔레그램 봇 데몬 로그
│   └── git.log               # Git pull 동기화 로그
├── session.json              # [Git 제외] 원자적으로 관리되는 세션 쿠키
├── scraper.lock              # [Git 제외] flock용 단일 인스턴스 잠금 파일
└── tests/                    # 단위 테스트 스위트
    └── test_collector.py
```

---

## ⚙️ 사전 설정 및 설치 (Installation)

### 1. Termux 기본 패키지 설치
안드로이드 Termux 터미널에서 다음 명령어를 실행합니다:

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git openssh cronie termux-api
```

### 2. 프로젝트 클론 및 의존성 설치

```bash
git clone git@github.com:<your-username>/instagram-scraper.git
cd instagram-scraper
pip install -r requirements.txt
```

### 3. 환경 변수 설정 (`.env`)

`.env.example`을 복사하여 `.env`를 생성하고 본인의 설정값을 입력합니다:

```bash
cp .env.example .env
nano .env
```

```ini
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNO_...
ADMIN_CHAT_ID=987654321

# Instagram Scraper Configuration
# 여러 계정일 경우 쉼표(,)로 구분 (예: account1,account2)
TARGET_USERNAME=target_account_username

# Optional Configuration
DATA_DIR=data
LOG_DIR=logs
SESSION_FILE_PATH=session.json
JITTER_MAX_SECONDS=60
REQUEST_TIMEOUT=15
```

---

## 🤖 텔레그램 봇 명령어 명세 (Telegram Bot Commands)

텔레그램 봇은 `ADMIN_CHAT_ID`로 지정된 관리자의 명령만 수신하며, 인가되지 않은 사용자의 요청은 안전하게 무시(Drop)됩니다.

| 명령어 | 매개변수 | 설명 |
|---|---|---|
| `/status` | 없음 | 배터리 잔량, 디스크 여유 공간, 최근 수집 성공 시각, 세션 상태 및 누적 수집 통계 리포트 |
| `/session` | `<sessionid>` | 새로운 세션 쿠키를 수신하여 `session.json`에 원자적(Atomic)으로 덮어쓰기 |
| `/run` | 없음 | 스케줄과 무관하게 스크래핑 1회 즉시 실행 (테스트 및 수동 수집용) |
| `/log` | `[lines]` | 최근 DB 수집 이력 및 `logs/cron.log` 최근 줄 출력 (기본값: 15줄) |
| `/help` | 없음 | 사용 가능한 명령어 가이드 출력 |

---

## ⏰ 배포 및 스케줄러 설정 (Cron & Scheduling)

### 1. Crontab 설정
평일(월~금) 10:00부터 11:50까지 10분 간격으로 총 12회 실행하며, 중복 실행 방지를 위해 `flock`을 적용합니다.

```bash
crontab -e
```

아래 설정을 추가합니다:

```cron
# 평일 10:00 ~ 11:50 (10:00, 10:10 ... 11:50) 매 10분마다 실행
*/10 10,11 * * 1-5 flock -n /data/data/com.termux/files/home/instagram-scraper/scraper.lock -c "cd /data/data/com.termux/files/home/instagram-scraper && git pull --rebase origin main >> logs/git.log 2>&1 && python scraper.py >> logs/cron.log 2>&1"
```

---

## 📱 OS 지속성 및 자동 실행 (`Termux:Boot`)

1. **Wake Lock 활성화:** 절전 모드 진입 방지
   ```bash
   termux-wake-lock
   ```
2. **Android OS 배터리 최적화 제외:**
   - 안드로이드 설정 → 애플리케이션 → Termux → 배터리 → **제한 없음(최적화 안 함)**으로 설정

3. **기기 재부팅 시 자동 시작 구성:**
   `Termux:Boot` 앱 설치 후 `~/.termux/boot/start-services.sh` 등록:

   ```bash
   mkdir -p ~/.termux/boot
   cp start_services.sh ~/.termux/boot/start-services.sh
   chmod +x ~/.termux/boot/start-services.sh
   ```

---

## 🧪 테스트 실행

```bash
python -m unittest discover tests
```
