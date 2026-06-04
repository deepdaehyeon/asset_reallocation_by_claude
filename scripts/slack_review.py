"""stdin으로 받은 리뷰 텍스트를 Slack 채널로 전송.

review_and_notify.sh에서 헤드리스 claude의 출력을 파이프로 받아 게시한다.
채널/토큰은 기존 messenger.py와 동일한 설정을 재사용.
"""
import os
import sys

import slack_sdk

CHANNEL = "C02SGLQV529"


def main() -> int:
    text = sys.stdin.read().strip()
    if not text:
        print("[slack_review] 빈 입력, skip")
        return 0

    token = os.getenv("SLACK_TOKEN")
    if not token:
        print("[slack_review] SLACK_TOKEN 없음, skip")
        return 0

    header = ":memo: *데일리 트레이딩 리뷰*"
    body = f"{header}\n{text}"

    client = slack_sdk.WebClient(token=token)
    try:
        client.chat_postMessage(channel=CHANNEL, text=body)
        print("[slack_review] 전송 완료")
    except Exception as e:
        print(f"[slack_review] 전송 실패: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
