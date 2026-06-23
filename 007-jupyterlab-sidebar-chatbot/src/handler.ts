// 노트북 커널에서 띄운 챗봇 서버(localhost)를 호출하는 fetch 헬퍼.
//
// jupyter 서버 익스텐션이 아니라, 노트북 셀에서 start_graph_server() 로 띄운
// http://127.0.0.1:<DEFAULT_PORT> 의 작은 HTTP 서버에 직접 요청합니다.
// (그래서 jupyter 서버 재시작이 전혀 필요 없습니다.)
//
// 전제: 브라우저의 127.0.0.1 == 커널의 127.0.0.1 (로컬/컨테이너/포트포워딩 환경).

// 백엔드(jlab_sidebar_chatbot/server.py)의 DEFAULT_PORT 와 반드시 일치해야 합니다.
export const DEFAULT_PORT = 8765;

/** 서버가 아직 안 떠 있을 때 사용자에게 보여줄 안내(셀에서 먼저 실행하라는). */
export const START_HINT =
  '챗봇 서버가 아직 안 떠 있어요. 노트북 셀에서 먼저 실행하세요:\n' +
  'from jlab_sidebar_chatbot import start_graph_server; start_graph_server()';

function baseUrl(port: number = DEFAULT_PORT): string {
  return `http://127.0.0.1:${port}`;
}

/**
 * 챗봇 서버 엔드포인트를 호출하고 JSON 을 돌려줍니다.
 *
 * @param endPoint 'chat' | 'reset' 등
 * @param init     fetch 옵션(method, body 등)
 */
export async function requestBrain<T>(
  endPoint: string,
  init: RequestInit = {},
  port: number = DEFAULT_PORT
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl(port)}/${endPoint}`, {
      ...init,
      // 다른 출처(localhost:다른포트)라 자격증명은 보내지 않습니다.
      headers: { 'Content-Type': 'application/json', ...(init.headers || {}) }
    });
  } catch (error) {
    // 연결 거부 = 서버 미기동. 사용자에게 시작 방법을 알려줍니다.
    throw new Error(START_HINT);
  }

  if (!response.ok) {
    let detail = '';
    try {
      detail = JSON.stringify(await response.json());
    } catch {
      // 본문 파싱 실패는 무시
    }
    throw new Error(`서버 오류 HTTP ${response.status} ${detail}`);
  }

  return (await response.json()) as T;
}

/**
 * SSE(text/event-stream) 스트리밍 엔드포인트(/chat/stream)를 호출합니다.
 *
 * EventSource 는 GET 전용이라 본문 POST 와 안 맞아, fetch 의 ReadableStream 을
 * 직접 읽어 SSE 프레임(event:/data:)을 파싱합니다.
 *
 * @param endPoint 'chat/stream'
 * @param body     요청 JSON (예: {session_id, message})
 * @param onToken  'token' 이벤트가 올 때마다 호출 — 답변 조각을 화면에 이어붙이는 콜백
 * @returns        'done' 이벤트의 페이로드(권위 있는 최종 {answer, steps})
 */
export async function streamBrain<T>(
  endPoint: string,
  body: unknown,
  onToken: (text: string) => void,
  port: number = DEFAULT_PORT
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl(port)}/${endPoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
  } catch (error) {
    // 연결 거부 = 서버 미기동. 사용자에게 시작 방법을 알려줍니다.
    throw new Error(START_HINT);
  }

  if (!response.ok || !response.body) {
    let detail = '';
    try {
      detail = JSON.stringify(await response.json());
    } catch {
      // 본문 파싱 실패는 무시
    }
    throw new Error(`서버 오류 HTTP ${response.status} ${detail}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result: unknown = {};
  // 'done' 이벤트가 이 스트림의 '끝' 신호입니다. 이게 오면 더 안 읽고 마칩니다.
  let finished = false;

  // 한 SSE 프레임("event: x\ndata: y") 을 해석합니다.
  const handleFrame = (frame: string): void => {
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) {
        event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim());
      }
    }
    if (!dataLines.length) {
      return;
    }
    const payload = JSON.parse(dataLines.join('\n'));
    if (event === 'token') {
      onToken(payload.text ?? '');
    } else if (event === 'done') {
      result = payload;
      finished = true; // 권위 있는 최종 결과 도착 — 루프를 끝낼 신호
    } else if (event === 'error') {
      throw new Error(payload.error || '스트리밍 오류');
    }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    // SSE 프레임은 빈 줄(\n\n) 로 구분됩니다.
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      if (frame.trim()) {
        handleFrame(frame);
      }
    }
    // 'done' 을 받으면 연결이 닫히길 기다리지 않고 즉시 종료합니다.
    // (서버가 keep-alive 로 소켓을 열어두면 reader.read() 가 영원히 안 끝나
    //  최종 렌더가 멈추는 문제를 방지 — done 이 곧 끝 신호이므로 안전)
    if (finished) {
      try {
        await reader.cancel();
      } catch {
        // 취소 중 오류는 무시 (이미 결과는 확보됨)
      }
      break;
    }
  }

  return result as T;
}
