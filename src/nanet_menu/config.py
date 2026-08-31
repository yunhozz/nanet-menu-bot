from dataclasses import dataclass

RETRY_WORKFLOW_URL = "https://github.com/yunhozz/nanet-menu-bot/actions/workflows/daily-menu.yml"


@dataclass(frozen=True)
class Settings:
    list_url: str = (
        "https://www.nanet.go.kr/usermadang/notice/noticeList.do"
        "?searchListGbn=&searchNoSeq=&pageIndex=1"
        "&searchKeycode=ticon&searchKeyword=%EC%8B%9D%EB%8B%A8"
    )
    timeout: tuple[float, float] = (10.0, 30.0)
    user_agent: str = "nanet-menu-bot/0.1 (+https://github.com/)"
