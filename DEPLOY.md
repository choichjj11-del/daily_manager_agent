# 데일리 매니저 배포 가이드

## 사전 준비
1. BotFather에서 새 봇 생성: @DailyManagerBot
2. 봇 토큰 확보
3. Brian 텔레그램 채팅 ID 확인 (기존: 7853728816)
4. OpenAI API 키 확보 (Whisper용)

## VPS 배포 명령어 (순서대로 실행)

```bash
# 1. 디렉토리 생성
mkdir -p /root/daily-manager
mkdir -p /data/daily-manager/chat_history

# 2. 파일 업로드 (main.py, requirements.txt, Dockerfile을 /root/daily-manager/에 넣기)

# 3. 환경변수 파일 생성
cat > /root/daily-manager/.env << 'EOF'
DAILY_MANAGER_BOT_TOKEN=여기에_봇_토큰
BRIAN_CHAT_ID=7853728816
OPENAI_API_KEY=여기에_OpenAI_키
DATA_DIR=/data/daily-manager
TZ=Asia/Seoul
EOF

# 4. Docker 이미지 빌드
cd /root/daily-manager
docker build -t daily-manager .

# 5. 컨테이너 실행
docker run -d \
  --name daily-manager \
  --restart always \
  --env-file /root/daily-manager/.env \
  -v /data/daily-manager:/data/daily-manager \
  daily-manager

# 6. 로그 확인
docker logs -f daily-manager

# 7. 정상 작동 확인
# 텔레그램에서 봇에게 /start 보내기
```

## 중지/재시작
```bash
docker stop daily-manager
docker start daily-manager
docker restart daily-manager
```

## 로그 확인
```bash
docker logs --tail 50 daily-manager
```

## 데이터 확인
```bash
cat /data/daily-manager/streaks.json
cat /data/daily-manager/todos_today.md
cat /data/daily-manager/revenue_log.md
```
