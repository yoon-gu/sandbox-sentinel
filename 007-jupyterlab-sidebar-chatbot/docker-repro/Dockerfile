# 폐쇄망 Pod(JupyterHub single-user 서버) 재현용 이미지.
#
# 이 이미지는 '운영 이미지 자체'가 아니라, 문제를 일으키는 **네트워크 토폴로지**를
# 재현합니다:
#   1) 컨테이너에서 8888'만' publish (compose 에서) → 호스트 브라우저는 컨테이너의
#      8765 에 닿지 못함. 즉 현재 HTTP 전송(handler.ts → http://127.0.0.1:8765)이
#      ERR_CONNECTION_REFUSED 로 실패 = 실제 Pod 에서 본 그 증상.
#   2) JupyterHub 처럼 base_url(/user/<id>/<server>/) 을 붙여 URL 경로까지 흉내.
#
# 컨테이너 = Pod, 호스트 브라우저 = 당신 노트북/PC, publish 된 8888 = ingress.
FROM python:3.11-slim

# 디버깅 편의 도구(slim 에는 없음): curl(엔드포인트 확인) · iproute2(ss 로 LISTEN 포트 확인).
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl iproute2 \
 && rm -rf /var/lib/apt/lists/*

# JupyterLab 4.5 + 두뇌(deepagents) + 모델 어댑터(openai/ollama).
# (실제 환경과 같은 버전대로 맞춤 — 자세한 버전 정책은 environment-adapter Skill 소관)
RUN pip install --no-cache-dir \
      "jupyterlab>=4.5,<5" \
      deepagents \
      langchain-openai \
      langchain-ollama

# 우리 확장 휠 설치(프론트 labextension 자산 포함). 호스트에서 미리 빌드한 dist/*.whl.
COPY dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl \
 && jupyter labextension list 2>&1 | grep -i chatbot || true

# 작업 폴더(노트북). compose 가 호스트의 007 폴더를 여기에 마운트합니다.
RUN mkdir -p /work
WORKDIR /work

EXPOSE 8888
