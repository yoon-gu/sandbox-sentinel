// LangGraph Server(`langgraph dev`)의 네이티브 API 를 호출하는 헬퍼.
//
// 커스텀 /chat 엔드포인트 대신 LangGraph 플랫폼 API 를 그대로 씁니다:
//   POST /threads                      → { thread_id }
//   POST /threads/{id}/runs/wait       → 최종 state(values). 멀티턴은 thread 가 관리.
// (langgraph.json 의 http.cors 로 프론트(다른 origin)에서의 호출을 허용.)
//
// 전제: 브라우저의 127.0.0.1 과 langgraph dev 서버가 같은 기계.

// LangGraph Server 기본 포트 (langgraph dev --port 2024) + langgraph.json 의 graph id.
export const LANGGRAPH_PORT = 2024;
export const ASSISTANT_ID = 'chatbot';

export const START_HINT =
  'LangGraph 서버에 연결할 수 없어요. 터미널에서 먼저 실행하세요:\n' +
  'cd 007-jupyterlab-sidebar-chatbot && langgraph dev --allow-blocking';

function baseUrl(port: number = LANGGRAPH_PORT): string {
  return `http://127.0.0.1:${port}`;
}

async function lgFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers || {}) }
    });
  } catch (error) {
    // 연결 거부 = 서버 미기동
    throw new Error(START_HINT);
  }
  if (!response.ok) {
    let detail = '';
    try {
      detail = JSON.stringify(await response.json());
    } catch {
      // 본문 파싱 실패 무시
    }
    throw new Error(`LangGraph 서버 오류 HTTP ${response.status} ${detail}`);
  }
  return (await response.json()) as T;
}

/** 새 대화 thread 를 만들고 thread_id 를 반환합니다. */
export async function createThread(): Promise<string> {
  const data = await lgFetch<{ thread_id: string }>('/threads', {
    method: 'POST',
    body: '{}'
  });
  return data.thread_id;
}

/** thread 에서 한 턴 실행(runs/wait)하고 마지막 assistant 텍스트를 반환합니다. */
export async function runWait(threadId: string, message: string): Promise<string> {
  const out = await lgFetch<{ messages?: any[] }>(
    `/threads/${threadId}/runs/wait`,
    {
      method: 'POST',
      body: JSON.stringify({
        assistant_id: ASSISTANT_ID,
        input: { messages: [{ role: 'user', content: message }] }
      })
    }
  );
  const messages = out.messages || [];
  const last = messages[messages.length - 1];
  let content = last && last.content;
  if (Array.isArray(content)) {
    content = content
      .map((b: any) => (typeof b === 'string' ? b : b.text || ''))
      .join('');
  }
  return typeof content === 'string' && content ? content : '(빈 응답)';
}
