# 전자공학기초실험 8조 팀플 프로젝트

## 소개
이 프로젝트는 8조 팀플의 서버/임베디드 연동입니다.
`smartswitch_embedded.c`는 임베디드용 C 코드이고, `new_server.py`는 서버용 Python 코드입니다.
`index.html`은 서버 파일과 같은 폴더에 두어 웹 UI와 함께 사용할 수 있도록 합니다.

## 파일 설명

- `smartswitch_embedded.c` - 임베디드 장치용 C 코드
- `new_server.py` - 서버용 Python 스크립트
- `index.html` - 웹 UI 파일 (서버 파일과 동일 폴더에 위치)

## 참고
- `index.html`은 서버와 함께 배치하여 웹 페이지 접근이 용이하도록 합니다.
- `smartswitch_embedded.c`는 임베디드 환경에서 동작하는 코드로, 서버 코드와 별개로 관리합니다.
- 삼성 Smartthings 통신을 위해선 인증서 파일이 폴더에 있어야 합니다.

## pcb
- pcb 파일 속 부품 라이브러리는 따로 추가하지 않았습니다.

