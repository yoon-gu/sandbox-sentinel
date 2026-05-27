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
