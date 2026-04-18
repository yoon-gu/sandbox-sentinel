# references/

이 디렉토리는 Code Conversion Agent가 변환 작업을 수행할 때 참조하는
**환경 스택 정의**, **API 매핑** 등을 담습니다. 자세한 설계 배경은 루트의 `CLAUDE.md` 참고.

## 구조

```
references/
├── README.md              # 이 문서
├── allowed-stacks/        # 환경별 허용/금지 패키지·라이선스 정책 정의
│   └── default.yaml       # 기본 가정 스택 (active_stack.txt가 가리킴)
└── api-mappings/          # 라이브러리 버전별 API 변경 매핑 (필요 시 추가)
```

## 활성 스택 결정 규칙

1. 리포 루트의 `active_stack.txt` 의 한 줄이 활성 스택 이름.
2. 사용자가 메시지로 환경 스펙을 직접 제공하면 해당 메시지 우선.
3. 둘 다 없으면 `default.yaml` 가정 + 사용자에게 확인.

## 새 스택 추가

`allowed-stacks/<env-name>.yaml` 형태로 파일을 추가하면 됩니다. YAML 스키마는
`default.yaml` 의 주석을 참고하세요.
