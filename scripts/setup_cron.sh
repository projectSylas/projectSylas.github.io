#!/bin/bash
# 로컬 macOS cron 설정 스크립트
# ./scripts/setup_cron.sh 실행

BLOG_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(which python3)"
LOG_DIR="$BLOG_ROOT/scripts/logs"
mkdir -p "$LOG_DIR"

echo "블로그 루트: $BLOG_ROOT"
echo "Python: $PYTHON"

# crontab 항목 생성
# 매일 오전 9시 블로그 포스팅
# 매일 오전 10시 LinkedIn 포스팅
CRON_BLOG="0 9 * * * cd $BLOG_ROOT && OPENAI_API_KEY=\$OPENAI_API_KEY $PYTHON scripts/auto_post.py >> $LOG_DIR/blog.log 2>&1"
CRON_LINKEDIN="0 10 * * * cd $BLOG_ROOT && OPENAI_API_KEY=\$OPENAI_API_KEY LINKEDIN_ACCESS_TOKEN=\$LINKEDIN_ACCESS_TOKEN LINKEDIN_PERSON_URN=\$LINKEDIN_PERSON_URN $PYTHON scripts/linkedin_post.py >> $LOG_DIR/linkedin.log 2>&1"

echo ""
echo "다음 내용을 'crontab -e'에 추가하세요:"
echo "=============================================="
echo "$CRON_BLOG"
echo "$CRON_LINKEDIN"
echo "=============================================="
echo ""
echo "⚠️  macOS cron은 환경변수를 상속하지 않습니다."
echo "   .env 파일을 사용하거나, crontab에 직접 환경변수 값을 입력하세요."
echo ""
echo "LaunchAgent 방식 (권장)을 원하면 launchd plist를 생성합니다."
echo "계속하려면 Enter, 건너뛰려면 Ctrl+C..."
read -r

# launchd plist 생성 (GitHub Actions가 없는 경우 권장)
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/io.github.projectsylas.blog-autopost.plist"

cat > "$PLIST_FILE" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.github.projectsylas.blog-autopost</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$BLOG_ROOT/scripts/auto_post.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$BLOG_ROOT</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OPENAI_API_KEY</key>
        <string>여기에_API_KEY_입력</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/blog.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/blog_error.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF

echo "[생성] $PLIST_FILE"
echo ""
echo "⚠️  plist 파일을 열어 OPENAI_API_KEY 값을 직접 입력하세요:"
echo "    open $PLIST_FILE"
echo ""
echo "활성화:"
echo "    launchctl load $PLIST_FILE"
echo ""
echo "비활성화:"
echo "    launchctl unload $PLIST_FILE"
echo ""
echo "즉시 테스트 실행:"
echo "    launchctl start io.github.projectsylas.blog-autopost"
