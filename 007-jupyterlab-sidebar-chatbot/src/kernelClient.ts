// 커널-Comm 전송 계층 — HTTP(127.0.0.1:8765) 대신 '커널 웹소켓'을 타고 두뇌와 통신.
//
// 왜 Comm 인가?
//   - 원격/컨테이너(JupyterHub Pod 등)에서는 브라우저가 커널의 8765 포트에 직접 닿지
//     못합니다(= ERR_CONNECTION_REFUSED). 하지만 노트북이 이미 쓰는 '커널 연결'
//     (JupyterHub 가 프록시하는 /user/.../api/kernels/<id>/channels)에는 닿습니다.
//   - Jupyter Comm 은 바로 그 커널 채널을 타므로 새 포트·CORS·서버변경 없이 동작합니다.
//
// 커널 쪽 짝: jlab_sidebar_chatbot/comm.py 의 register_chatbot_comm().
//   프론트 → 커널 :  {type:'message', message, session_id}  /  {type:'reset', session_id}
//   커널 → 프론트 :  {type:'token', text}  /  {type:'done', answer, steps}
//                   {type:'error', error}  /  {type:'reset_ok'}  /  {type:'ready'}

import { INotebookTracker } from '@jupyterlab/notebook';
import { Kernel, KernelMessage } from '@jupyterlab/services';

// ⚠️ comm.py 의 COMM_TARGET 과 반드시 일치해야 합니다(server.py↔handler.ts 의 포트 약속과 같은 역할).
const COMM_TARGET = 'jlab_sidebar_chatbot';

// 노트북/커널이 없을 때(또는 셀에서 register 를 안 했을 때) 사용자에게 보여줄 안내.
export const KERNEL_HINT =
  '커널을 찾을 수 없어요. 노트북을 하나 열고(커널 실행) 셀에서 먼저 등록하세요:\n' +
  'from jlab_sidebar_chatbot import register_chatbot_comm; ' +
  'register_chatbot_comm(provider="ollama", model="qwen3.5:0.8b")';

/** 한 턴의 중간 단계(도구 호출/결과/생각). */
interface ChatStep {
  type: string;
  label: string;
  detail: string;
}

/** done 응답 형태(= comm.py 의 stream_turn done 페이로드). */
export interface ChatReply {
  answer?: string;
  steps?: ChatStep[];
}

/** 진행 중인 한 턴의 콜백 묶음. */
interface PendingTurn {
  onToken: (text: string) => void;
  resolve: (reply: ChatReply) => void;
  reject: (err: Error) => void;
}

/**
 * 현재 노트북 커널에 Comm 으로 붙어 한 턴씩 대화하는 클라이언트.
 *
 * 한 위젯(세션)당 하나. 메시지는 직렬로만 보냅니다(위젯이 전송 중 입력을 잠금) —
 * 그래서 '진행 중인 턴' 하나만 추적하면 응답을 정확히 짝지을 수 있습니다.
 */
export class KernelChatClient {
  private _tracker: INotebookTracker;
  private _comm: Kernel.IComm | null = null;
  private _kernelId: string | null = null;
  private _pending: PendingTurn | null = null; // 진행 중인 message 턴
  private _resetResolve: (() => void) | null = null; // 진행 중인 reset
  private _ready = false; // 현재 comm 이 'ready' 를 받았는지(=커널에 target 이 등록돼 있는지)

  constructor(tracker: INotebookTracker) {
    this._tracker = tracker;
  }

  /** 현재 활성 노트북의 커널(없으면 null). */
  private _currentKernel(): Kernel.IKernelConnection | null {
    return this._tracker.currentWidget?.sessionContext?.session?.kernel ?? null;
  }

  /** 살아있는 comm 을 보장합니다(커널이 바뀌었으면 새로 엶). 커널이 없으면 에러. */
  private _ensureComm(): Kernel.IComm {
    const kernel = this._currentKernel();
    if (!kernel) {
      throw new Error(KERNEL_HINT);
    }
    // 같은 커널에 이미 열린 comm 이 있으면 재사용.
    if (this._comm && !this._comm.isDisposed && this._kernelId === kernel.id) {
      return this._comm;
    }
    // 커널이 바뀌었거나 comm 이 없으면 새로 만든다.
    const comm = kernel.createComm(COMM_TARGET);
    this._ready = false; // 새 comm — 아직 'ready' 안 받음
    comm.onMsg = (msg: KernelMessage.ICommMsgMsg) => this._onMsg(msg);
    comm.onClose = () => {
      // 커널이 comm 을 닫는 경우는 둘 중 하나:
      //   (a) target 미등록 → open 직후 즉시 닫힘('ready' 를 못 받음) → 셀 실행 안내
      //   (b) 도중에 커널이 죽음/재시작 → 연결 끊김 안내
      const wasReady = this._ready;
      this._comm = null;
      this._kernelId = null;
      this._ready = false;
      if (this._pending) {
        this._pending.reject(
          new Error(
            wasReady
              ? '커널 연결이 닫혔습니다 (커널이 재시작/중지됐을 수 있어요).'
              : KERNEL_HINT
          )
        );
        this._pending = null;
      }
    };
    // open() → 커널 쪽 on_open 이 불려 핸들러가 붙고 {type:'ready'} 가 옵니다.
    // (comm_open 과 이어지는 comm_msg 는 같은 채널에서 순서가 보장되므로,
    //  open() 직후 send() 해도 커널은 'open→msg' 순으로 처리합니다.)
    comm.open({});
    this._comm = comm;
    this._kernelId = kernel.id;
    return comm;
  }

  /** 커널→프론트 메시지 1건 처리. */
  private _onMsg(msg: KernelMessage.ICommMsgMsg): void {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const data = (msg.content.data ?? {}) as any;
    const type = data.type as string;

    if (type === 'reset_ok') {
      if (this._resetResolve) {
        this._resetResolve();
        this._resetResolve = null;
      }
      return;
    }
    if (type === 'ready') {
      this._ready = true; // 커널에 target 이 등록돼 있고 comm 이 열렸다는 신호
      return;
    }
    // 이하 message 턴 응답. 진행 중 턴이 없으면 무시(레이스 방지).
    if (!this._pending) {
      return;
    }
    if (type === 'token') {
      this._pending.onToken(String(data.text ?? ''));
    } else if (type === 'done') {
      const p = this._pending;
      this._pending = null;
      p.resolve({ answer: data.answer, steps: data.steps });
    } else if (type === 'error') {
      const p = this._pending;
      this._pending = null;
      p.reject(new Error(String(data.error ?? '알 수 없는 오류')));
    }
  }

  /** 한 턴: 메시지를 보내고 토큰을 onToken 으로 흘리며 done 의 결과를 반환합니다. */
  async stream(
    sessionId: string,
    message: string,
    onToken: (text: string) => void
  ): Promise<ChatReply> {
    const comm = this._ensureComm();
    if (this._pending) {
      throw new Error('이전 응답이 아직 진행 중입니다.');
    }
    return new Promise<ChatReply>((resolve, reject) => {
      this._pending = { onToken, resolve, reject };
      try {
        comm.send({ type: 'message', message, session_id: sessionId });
      } catch (err) {
        this._pending = null;
        reject(err as Error);
      }
    });
  }

  /** 세션 기록 리셋(커널의 thread 를 새로 분기). */
  async reset(sessionId: string): Promise<void> {
    const comm = this._ensureComm();
    return new Promise<void>(resolve => {
      this._resetResolve = resolve;
      try {
        comm.send({ type: 'reset', session_id: sessionId });
      } catch {
        this._resetResolve = null;
        resolve();
        return;
      }
      // 안전장치: reset_ok 가 안 와도 화면이 멈추지 않게 짧은 타임아웃 후 resolve.
      window.setTimeout(() => {
        if (this._resetResolve) {
          this._resetResolve = null;
          resolve();
        }
      }, 1500);
    });
  }
}
