// 우측 사이드바에 탭으로 도킹되는 챗봇 위젯(Lumino Widget).
//
// 구조는 공식 예제 jupyterlab/extension-examples 의 shout-button-message 를 따릅니다.
//   - Widget 을 상속하고 생성자에서 DOM 을 구성
//   - 이벤트 리스너는 onAfterAttach 에서 등록하고 onBeforeDetach 에서 해제
// (예제는 addEventListener/removeEventListener 에 each `this.shout.bind(this)` 를
//  넘겨 실제로는 해제가 안 되는 작은 버그가 있습니다. 여기서는 동일한 함수 참조를
//  유지하도록 화살표 함수 필드로 바인딩해 정확히 해제되게 했습니다.)

import { Message } from '@lumino/messaging';
import { Widget } from '@lumino/widgets';

import { requestBrain } from './handler';

interface ChatMessage {
  role: string; // 'user' | 'assistant' | 'error'
  content: string;
}

/** 챗봇 사이드바 위젯. */
export class ChatWidget extends Widget {
  private _sessionId: string;
  private _messagesNode: HTMLDivElement;
  private _input: HTMLTextAreaElement;
  private _sendButton: HTMLElement;
  private _newButton: HTMLElement;

  constructor() {
    super();
    this.addClass('jp-ChatWidget');

    // 위젯마다 고유 세션 id — 서버가 대화 맥락을 이 키로 구분합니다.
    this._sessionId = `jlsc-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;

    // ── 헤더: 제목 + 새 대화 버튼 ──
    const header = document.createElement('div');
    header.classList.add('jp-ChatWidget-header');

    const title = document.createElement('span');
    title.classList.add('jp-ChatWidget-title');
    title.innerText = '💬 Chatbot';

    this._newButton = document.createElement('button');
    this._newButton.classList.add('jp-ChatWidget-newButton');
    this._newButton.innerText = '새 대화';
    this._newButton.title = '대화 기록을 비우고 새로 시작합니다';

    header.appendChild(title);
    header.appendChild(this._newButton);

    // ── 메시지 목록(스크롤 영역) ──
    this._messagesNode = document.createElement('div');
    this._messagesNode.classList.add('jp-ChatWidget-messages');

    // ── 입력 영역: textarea + 전송 버튼 ──
    const inputRow = document.createElement('div');
    inputRow.classList.add('jp-ChatWidget-inputRow');

    this._input = document.createElement('textarea');
    this._input.classList.add('jp-ChatWidget-input');
    this._input.rows = 2;
    this._input.placeholder = '메시지를 입력하세요 (Enter: 전송 · Shift+Enter: 줄바꿈)';

    this._sendButton = document.createElement('button');
    this._sendButton.classList.add('jp-ChatWidget-sendButton');
    this._sendButton.innerText = '전송';

    inputRow.appendChild(this._input);
    inputRow.appendChild(this._sendButton);

    this.node.appendChild(header);
    this.node.appendChild(this._messagesNode);
    this.node.appendChild(inputRow);

    // 첫 인사(로컬에서만 추가, 서버 호출 아님).
    this._appendMessage({
      role: 'assistant',
      content: '안녕하세요! 무엇이든 입력해 보세요. (deepagents·langgraph + Claude)'
    });
  }

  /** 위젯이 DOM 에 붙을 때: 이벤트 리스너 등록. */
  protected onAfterAttach(msg: Message): void {
    super.onAfterAttach(msg);
    this._sendButton.addEventListener('click', this._handleSend);
    this._newButton.addEventListener('click', this._handleReset);
    this._input.addEventListener('keydown', this._handleKeydown);
  }

  /** 위젯이 DOM 에서 떨어질 때: 이벤트 리스너 해제. */
  protected onBeforeDetach(msg: Message): void {
    this._sendButton.removeEventListener('click', this._handleSend);
    this._newButton.removeEventListener('click', this._handleReset);
    this._input.removeEventListener('keydown', this._handleKeydown);
    super.onBeforeDetach(msg);
  }

  // 화살표 함수 필드 — add/removeEventListener 가 같은 참조를 쓰도록 보장.
  private _handleSend = (): void => {
    void this._send();
  };

  private _handleReset = (): void => {
    void this._resetChat();
  };

  private _handleKeydown = (ev: KeyboardEvent): void => {
    // Enter 전송, Shift+Enter 줄바꿈.
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      void this._send();
    }
  };

  /** 메시지 한 건을 말풍선으로 그려 목록에 추가합니다. */
  private _appendMessage(message: ChatMessage): HTMLDivElement {
    const bubble = document.createElement('div');
    bubble.classList.add('jp-ChatWidget-bubble', `jp-mod-${message.role}`);
    bubble.innerText = message.content;
    this._messagesNode.appendChild(bubble);
    // 항상 최신 메시지가 보이도록 스크롤을 맨 아래로.
    this._messagesNode.scrollTop = this._messagesNode.scrollHeight;
    return bubble;
  }

  /** 전송 중에는 입력/버튼을 잠가 중복 전송을 막습니다. */
  private _setBusy(busy: boolean): void {
    this._input.disabled = busy;
    (this._sendButton as HTMLButtonElement).disabled = busy;
  }

  /** 입력값을 서버로 보내고 응답을 받아 표시합니다. */
  private async _send(): Promise<void> {
    const text = this._input.value.trim();
    if (!text) {
      return;
    }

    this._appendMessage({ role: 'user', content: text });
    this._input.value = '';
    this._setBusy(true);

    // 응답을 기다리는 동안 "…" 자리표시 말풍선을 보여줍니다.
    const pending = this._appendMessage({ role: 'assistant', content: '…' });
    pending.classList.add('jp-mod-pending');

    try {
      const reply = await requestBrain<ChatMessage>('chat', {
        method: 'POST',
        body: JSON.stringify({ session_id: this._sessionId, message: text })
      });
      pending.classList.remove('jp-mod-pending');
      pending.innerText = reply.content;
    } catch (error) {
      pending.classList.remove('jp-mod-pending', 'jp-mod-assistant');
      pending.classList.add('jp-mod-error');
      // 서버 미기동이면 START_HINT 안내가, 그 외엔 서버 오류 메시지가 표시됩니다.
      pending.innerText = (error as Error).message;
      console.error('jlab-sidebar-chatbot 요청 실패:', error);
    } finally {
      this._setBusy(false);
      this._input.focus();
    }
  }

  /** 서버 세션을 초기화하고 화면을 비웁니다. */
  private async _resetChat(): Promise<void> {
    try {
      await requestBrain<{ ok: boolean }>('reset', {
        method: 'POST',
        body: JSON.stringify({ session_id: this._sessionId })
      });
    } catch (error) {
      console.error('reset 실패:', error);
    }
    this._messagesNode.innerHTML = '';
    this._appendMessage({
      role: 'assistant',
      content: '새 대화를 시작했습니다. 무엇을 도와드릴까요?'
    });
  }
}
