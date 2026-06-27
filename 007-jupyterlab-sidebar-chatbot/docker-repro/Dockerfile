# 폐쇄망 Pod(single-user Jupyter 서버) 재현용 이미지.
#
# 이 이미지는 '운영 이미지 자체'가 아니라, 문제를 일으키는 **네트워크 토폴로지**를
# 재현합니다:
#   - 컨테이너에서 ingress(8888)'만' publish (compose 에서) → 호스트 브라우저는
#     커널의 직접 포트에 닿지 못함. 따라서 두뇌(커널)에는 오직 Jupyter 프로토콜
#     (api/kernels/<id>/channels WebSocket = Comm)으로만 닿을 수 있음 = 실제 Pod 상황.
#   - url 은 jupyter 기본('/'). (이전의 JupyterHub base_url 흉내는 제거 — sandbox 표준 env)
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

# 다른 변환물(001~006)도 이 pod 안에서 검증하기 위한 의존성 (리포 requirements.txt 기준).
# ⚠️ langgraph/langchain 은 핀하지 않습니다 — deepagents(007/008)가 요구하는 1.2.x 를
#    유지해야 하므로 requirements.txt 의 langgraph==1.0.10 핀은 일부러 제외(공존 불가).
#    001/003/004 는 langgraph 1.2.x 에서 검증됩니다(원 핀과 다름은 알려진 트레이드오프).
RUN pip install --no-cache-dir \
      numpy pandas openpyxl ipywidgets rich prompt_toolkit psutil \
      tree-sitter tree-sitter-sql duckdb \
      textual==6.11.0

# 우리 확장 휠 설치(프론트 labextension 자산 포함). 호스트에서 미리 빌드한 dist/*.whl.
COPY dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl \
 && jupyter labextension list 2>&1 | grep -i chatbot || true

# 작업 폴더(노트북). compose 가 호스트의 007 폴더를 여기에 마운트합니다.
RUN mkdir -p /work
WORKDIR /work

EXPOSE 8888
