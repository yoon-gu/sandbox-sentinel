// 프론트엔드 플러그인 진입점.
//
// 공식 예제 shout-button-message 와 동일하게, 단일 플러그인이 위젯을 만들어
// app.shell.add(widget, 'right') 로 우측 사이드바에 "탭"으로 추가합니다.
// (예제와 달리 탭 안의 내용은 버튼이 아니라 챗 UI 이고, 사이드바 탭임을 알리는
//  아이콘/툴팁을 붙였습니다.)

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { LabIcon } from '@jupyterlab/ui-components';

import { ChatWidget } from './widget';

// 사이드바 탭에 표시할 말풍선 아이콘(인라인 SVG — 외부 자산 참조 없음).
const chatIconSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24">
  <path class="jp-icon3 jp-icon-selectable" fill="#616161"
    d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/>
  <path class="jp-icon3 jp-icon-selectable" fill="#616161"
    d="M6 7h12v2H6zm0 4h9v2H6z"/>
</svg>`;

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'jlab-sidebar-chatbot:plugin',
  description: 'JupyterLab 우측 사이드바에 챗봇 탭을 추가합니다.',
  autoStart: true,
  activate: (app: JupyterFrontEnd) => {
    console.log('JupyterLab extension jlab-sidebar-chatbot is activated!');

    // 챗봇 위젯을 만들어 우측 사이드바에 추가합니다(shout 예제와 동일한 패턴).
    const widget = new ChatWidget();
    widget.id = 'jlab-sidebar-chatbot-widget'; // Widget 은 id 가 필요합니다.
    widget.title.icon = new LabIcon({
      name: 'jlab-sidebar-chatbot:chat',
      svgstr: chatIconSvg
    });
    widget.title.caption = '챗봇'; // 사이드바 탭 위 마우스 툴팁

    app.shell.add(widget, 'right');
  }
};

export default plugin;
