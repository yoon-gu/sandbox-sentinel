// 우측 사이드바에 탭으로 도킹되는 챗봇 위젯(Lumino Widget).
//
// 구조는 공식 예제 jupyterlab/extension-examples 의 shout-button-message 를 따릅니다.
//   - Widget 을 상속하고 생성자에서 DOM 을 구성
//   - 이벤트 리스너는 onAfterAttach 에서 등록하고 onBeforeDetach 에서 해제
//
// 마크다운 렌더링:
//   - JupyterLab 의 jp-RenderedHTMLCommon CSS 를 안 거치고 markdown-it 으로 직접 렌더.
//   - 코드블록은 highlight.js (필요한 언어만 cherry-pick) 로 토큰화 — 색은
//     JupyterLab CSS 변수에 매핑(라이트/다크 자동 적응, style/base.css 참고).
//   - LLM 출력의 raw HTML/이벤트 핸들러는 DOMPurify 로 sanitize.
//   - 코드블록마다 우측 상단에 "복사" 버튼을 자동 부착(챗 UX).
//
// 도구·중간 단계(steps) 는 <details> 로 기본 접힘 — 클릭 시 펼침.

import { Message } from '@lumino/messaging';
import { Widget } from '@lumino/widgets';
import MarkdownIt from 'markdown-it';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import sql from 'highlight.js/lib/languages/sql';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';
import yaml from 'highlight.js/lib/languages/yaml';
import markdown from 'highlight.js/lib/languages/markdown';
import plaintext from 'highlight.js/lib/languages/plaintext';

import { requestBrain } from './handler';

// 폐쇄망에서 자주 쓰는 언어만 등록 — 번들 크기 절약(전체는 700KB+, 이건 ~25KB).
hljs.registerLanguage('python', python);
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('md', markdown);
hljs.registerLanguage('plaintext', plaintext);
hljs.registerLanguage('text', plaintext);

// 챗 전체에서 재사용하는 단일 markdown-it 인스턴스.
//   - html:false   — LLM 출력의 raw HTML 무력화(추가 안전망, sanitize 도 별도로 함)
//   - linkify:true — http://... 자동 링크
//   - breaks:true  — 챗 관행대로 단일 \n 을 <br> 로
//   - typographer:false — 스마트 따옴표 등은 코드 컨텍스트에서 거슬려 끔
const md: MarkdownIt = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
  highlight: (str, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      try {
        const result = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value;
        return `<pre class="hljs"><code class="hljs language-${lang}">${result}</code></pre>`;
      } catch {
        /* 폴백으로 떨어짐 */
      }
    }
    return `<pre class="hljs"><code class="hljs">${md.utils.escapeHtml(str)}</code></pre>`;
  }
});

interface ChatMessage {
  role: string; // 'user' | 'assistant' | 'error'
  content: string;
}

/** 한 턴의 중간 단계(도구 호출/결과/생각). */
interface ChatStep {
  type: string;
  label: string;
  detail: string;
}

/** /chat 응답 형태. */
interface ChatReply {
  answer?: string;
  steps?: ChatStep[];
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

    // 위젯마다 고유 세션 id — 서버가 대화 맥락(thread)을 이 키로 구분합니다.
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
      content: '안녕하세요! 무엇이든 입력해 보세요. (deepagents · langgraph + OpenAI 호환 모델)'
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

  /** 메시지 한 건을 말풍선(평문)으로 그려 목록에 추가합니다. */
  private _appendMessage(message: ChatMessage): HTMLDivElement {
    const bubble = document.createElement('div');
    bubble.classList.add('jp-ChatWidget-bubble', `jp-mod-${message.role}`);
    bubble.innerText = message.content;
    this._messagesNode.appendChild(bubble);
    this._scrollToBottom();
    return bubble;
  }

  private _scrollToBottom(): void {
    this._messagesNode.scrollTop = this._messagesNode.scrollHeight;
  }

  /** 최종 답변(마크다운)을 host 에 렌더 — markdown-it → DOMPurify → innerHTML. */
  private _renderMarkdown(host: HTMLElement, markdownText: string): void {
    const wrapper = document.createElement('div');
    wrapper.classList.add('jp-ChatWidget-markdown');
    try {
      // 1) 마크다운 → HTML 문자열
      const html = md.render(markdownText);
      // 2) sanitize — LLM 출력의 위험 요소(event handler, javascript:, data: 등) 제거
      wrapper.innerHTML = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
      // 3) 코드블록 우측 상단에 "복사" 버튼 부착
      this._attachCopyButtons(wrapper);
      // 4) 자동 링크는 새 탭으로(이미 sanitize 후이므로 안전)
      wrapper.querySelectorAll('a').forEach(a => {
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener noreferrer');
      });
    } catch {
      // 렌더 실패 시 평문 폴백
      wrapper.innerText = markdownText;
    }
    host.appendChild(wrapper);
    this._scrollToBottom();
  }

  /** 렌더된 <pre> 코드블록마다 우측 상단에 "복사" 버튼을 붙입니다. */
  private _attachCopyButtons(root: HTMLElement): void {
    root.querySelectorAll('pre').forEach(pre => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'jp-ChatWidget-copyBtn';
      btn.textContent = '복사';
      btn.title = '코드 복사';
      btn.addEventListener('click', e => {
        e.preventDefault();
        const code = pre.querySelector('code');
        const text = code?.textContent ?? '';
        const restore = () => {
          window.setTimeout(() => (btn.textContent = '복사'), 1500);
        };
        // 폐쇄망/HTTP 환경에서 clipboard API 가 막힌 경우를 위한 폴백
        const ok = () => {
          btn.textContent = '복사됨';
          restore();
        };
        const fail = () => {
          btn.textContent = '실패';
          restore();
        };
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(text).then(ok, fail);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          try {
            document.execCommand('copy');
            ok();
          } catch {
            fail();
          } finally {
            document.body.removeChild(ta);
          }
        }
      });
      pre.appendChild(btn);
    });
  }

  /** 도구/중간 단계를 기본-접힌 <details> 로 만듭니다(클릭 시 펼침). */
  private _buildSteps(steps: ChatStep[]): HTMLDetailsElement {
    const details = document.createElement('details');
    details.className = 'jp-ChatWidget-steps';

    const summary = document.createElement('summary');
    summary.innerText = `🔧 도구·중간 단계 ${steps.length}개 (클릭해서 펼치기)`;
    details.appendChild(summary);

    for (const step of steps) {
      const item = document.createElement('div');
      item.className = 'jp-ChatWidget-step';

      const label = document.createElement('div');
      label.className = 'jp-ChatWidget-stepLabel';
      label.innerText = step.label || step.type || 'step';
      item.appendChild(label);

      if (step.detail) {
        const detail = document.createElement('pre');
        detail.className = 'jp-ChatWidget-stepDetail';
        detail.innerText = step.detail;
        item.appendChild(detail);
      }
      details.appendChild(item);
    }
    return details;
  }

  /** 전송 중에는 입력/버튼을 잠가 중복 전송을 막습니다. */
  private _setBusy(busy: boolean): void {
    this._input.disabled = busy;
    (this._sendButton as HTMLButtonElement).disabled = busy;
  }

  /** 입력값을 서버로 보내고, 도구 단계(접힘) + 최종 답변(마크다운)을 표시합니다. */
  private async _send(): Promise<void> {
    const text = this._input.value.trim();
    if (!text) {
      return;
    }

    this._appendMessage({ role: 'user', content: text });
    this._input.value = '';
    this._setBusy(true);

    const pending = this._appendMessage({ role: 'assistant', content: '…' });
    pending.classList.add('jp-mod-pending');

    try {
      const reply = await requestBrain<ChatReply>('chat', {
        method: 'POST',
        body: JSON.stringify({ session_id: this._sessionId, message: text })
      });
      pending.classList.remove('jp-mod-pending');
      pending.innerText = '';
      // 도구·중간 단계는 접어서 먼저(클릭하면 펼침), 그 아래 최종 답변을 마크다운으로
      if (reply.steps && reply.steps.length) {
        pending.appendChild(this._buildSteps(reply.steps));
      }
      this._renderMarkdown(pending, reply.answer || '(빈 응답)');
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
