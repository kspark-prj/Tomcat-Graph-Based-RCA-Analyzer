import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry  # type: ignore

# 로깅 설정
logger = logging.getLogger("GitHubEnricher")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")


# ---------------------------------------------------------------------------
# 1. DTO / 데이터 모델
# ---------------------------------------------------------------------------
@dataclass
class CommitInfo:
    sha: str
    author_name: str
    author_email: str
    date: str
    message: str
    commit_url: str


@dataclass
class LineBlameInfo:
    line_number: int
    commit_sha: str
    author_name: str
    author_email: str
    commit_message: str
    deep_link_url: str


# ---------------------------------------------------------------------------
# 2. 캐시 메모리 누수 방지를 위한 모듈 레벨 독립 캐시 함수
# ---------------------------------------------------------------------------
@lru_cache(maxsize=512)
def _fetch_file_last_commit_cached(
    owner: str, repo: str, token: str, file_path: str, ref: str, timeout: int
) -> dict[str, Any] | None:
    """REST API 파일 커밋 조회 (self 참조를 제거하여 메모리 누수 방지)"""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"path": file_path, "sha": ref, "per_page": 1}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=timeout)
        if res.status_code == 404:
            logger.warning(f"파일을 찾을 수 없음 (404): {file_path}")
            return None
        res.raise_for_status()

        data = res.json()
        return data[0] if data else None
    except Exception as e:
        logger.error(f"REST API 커밋 조회 실패 ({file_path}): {e}")
        return None


@lru_cache(maxsize=1024)
def _fetch_line_blame_cached(
    owner: str,
    repo: str,
    token: str,
    file_path: str,
    line_number: int,
    ref: str,
    timeout: int,
) -> dict[str, Any] | None:
    """GraphQL API 라인 Blame 조회 (self 참조 제거 및 GraphQL 에러 검지)"""
    url = "https://api.github.com/graphql"
    headers = {"Authorization": f"Bearer {token}"}

    query = """
    query($owner: String!, $repo: String!, $expression: String!, $path: String!) {
      repository(owner: $owner, name: $repo) {
        object(expression: $expression) {
          ... on Commit {
            blame(path: $path) {
              ranges {
                startingLine
                endingLine
                commit {
                  oid
                  message
                  author { name email }
                }
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "owner": owner,
        "repo": repo,
        "expression": ref,
        "path": file_path,
    }

    try:
        res = requests.post(
            url,
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=timeout,
        )
        res.raise_for_status()
        data = res.json()

        # GraphQL 200 OK 응답 내 본문 에러 체크
        if "errors" in data:
            logger.error(f"GraphQL API 응답 에러 ({file_path}): {data['errors']}")
            return None

        ranges = (
            data.get("data", {})
            .get("repository", {})
            .get("object", {})
            .get("blame", {})
            .get("ranges", [])
        )

        for r in ranges:
            if r["startingLine"] <= line_number <= r["endingLine"]:
                return r["commit"]
    except Exception as e:
        logger.error(f"GraphQL Blame 조회 실패 ({file_path}:{line_number}): {e}")
    return None


# ---------------------------------------------------------------------------
# 3. 실무형 GitHub API Client 모듈
# ---------------------------------------------------------------------------
class GitHubEnricher:
    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        default_branch: str = "main",
        timeout: int = 5,
    ):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.default_branch = default_branch
        self.timeout = timeout
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}"

        # HTTP 세션 및 자동 재시도(Retry) 설정
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

        # 429(Rate Limit), 500, 502, 503, 504 응답 시 지연 후 최대 3회 재시도
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    # -----------------------------------------------------------------------
    # [실무 유틸 1] API 사용량(Rate Limit) 조회
    # -----------------------------------------------------------------------
    def get_rate_limit(self) -> dict[str, Any]:
        """현재 GitHub API 잔여 호출 횟수를 확인합니다."""
        url = "https://api.github.com/rate_limit"
        try:
            res = self.session.get(url, timeout=self.timeout)
            res.raise_for_status()
            core = res.json().get("resources", {}).get("core", {})
            graphql = res.json().get("resources", {}).get("graphql", {})
            return {
                "rest_remaining": core.get("remaining"),
                "rest_limit": core.get("limit"),
                "graphql_remaining": graphql.get("remaining"),
                "graphql_limit": graphql.get("limit"),
            }
        except Exception as e:
            logger.error(f"Rate Limit 조회 실패: {e}")
            return {}

    # -----------------------------------------------------------------------
    # [REST API] 특정 파일의 최근 커밋 정보 조회
    # -----------------------------------------------------------------------
    def get_file_last_commit(self, file_path: str, ref: str | None = None) -> CommitInfo | None:
        """특정 파일의 가장 최근 수정 커밋 및 작성자를 반환합니다."""
        target_ref = ref if ref else self.default_branch
        raw_commit = _fetch_file_last_commit_cached(
            self.owner,
            self.repo,
            self.token,
            file_path,
            target_ref,
            self.timeout,
        )

        if not raw_commit:
            return None

        commit_data = raw_commit["commit"]
        return CommitInfo(
            sha=raw_commit["sha"],
            author_name=commit_data["author"]["name"],
            author_email=commit_data["author"]["email"],
            date=commit_data["author"]["date"],
            message=commit_data["message"].split("\n")[0],
            commit_url=raw_commit["html_url"],
        )

    # -----------------------------------------------------------------------
    # [GraphQL API] specific 라인의 Git Blame 추적
    # -----------------------------------------------------------------------
    def get_line_blame(
        self, file_path: str, line_number: int, ref: str | None = None
    ) -> LineBlameInfo | None:
        """에러가 발생한 특정 라인의 마지막 수정자와 커밋 정보를 추출합니다."""
        target_ref = ref if ref else self.default_branch
        raw_blame = _fetch_line_blame_cached(
            self.owner,
            self.repo,
            self.token,
            file_path,
            line_number,
            target_ref,
            self.timeout,
        )

        if not raw_blame:
            return None

        commit_sha = raw_blame["oid"]
        deep_link = self.build_deep_link(file_path, line_number, commit_sha)

        return LineBlameInfo(
            line_number=line_number,
            commit_sha=commit_sha,
            author_name=raw_blame["author"]["name"],
            author_email=raw_blame["author"]["email"],
            commit_message=raw_blame["message"].split("\n")[0],
            deep_link_url=deep_link,
        )

    # -----------------------------------------------------------------------
    # [실무 유틸 2] 이슈 자동 생성 및 중복 방지 (Search API 이용)
    # -----------------------------------------------------------------------
    def report_issue_or_comment(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        동일한 제목의 이슈가 이미 Open 상태면 새 이슈를 만들지 않고 '댓글'을 달며,
        없으면 새로운 Issue를 작성합니다. (Search API 사용으로 50개 제한 보완)
        """
        try:
            # 1. Search API로 동일 제목의 Open 이슈 검색
            search_url = "https://api.github.com/search/issues"
            query_str = f'repo:{self.owner}/{self.repo} is:issue is:open in:title "{title}"'
            search_res = self.session.get(
                search_url,
                params={"q": query_str},
                timeout=self.timeout,
            )
            search_res.raise_for_status()

            items = search_res.json().get("items", [])
            target_issue = None
            for item in items:
                if item["title"] == title:
                    target_issue = item
                    break

            # 2. 이미 존재하는 이슈가 있다면 -> 댓글 작성 (Comment)
            if target_issue:
                issue_number = target_issue["number"]
                comment_url = f"{self.base_url}/issues/{issue_number}/comments"
                comment_body = f"⚠️ **동일 장애 추가 발생 감지**\n\n{body}"

                comment_res = self.session.post(
                    comment_url,
                    json={"body": comment_body},
                    timeout=self.timeout,
                )
                comment_res.raise_for_status()
                logger.info(f"기존 이슈 #{issue_number}에 코멘트 추가 완료")
                return {
                    "action": "commented",
                    "issue_url": target_issue["html_url"],
                }

            # 3. 신규 이슈 작성 (Create Issue)
            create_url = f"{self.base_url}/issues"
            payload = {"title": title, "body": body}
            if labels:
                payload["labels"] = labels
            if assignees:
                payload["assignees"] = assignees

            create_res = self.session.post(create_url, json=payload, timeout=self.timeout)
            create_res.raise_for_status()
            new_issue = create_res.json()
            logger.info(f"신규 이슈 #{new_issue['number']} 생성 완료")
            return {"action": "created", "issue_url": new_issue["html_url"]}

        except Exception as e:
            logger.error(f"GitHub Issue 처리 중 오류 발생: {e}")
            return {"action": "failed", "error": str(e)}

    # -----------------------------------------------------------------------
    # [실무 유틸 3] Deep Link URL 생성
    # -----------------------------------------------------------------------
    def build_deep_link(
        self,
        file_path: str,
        line_number: int,
        commit_sha: str | None = None,
    ) -> str:
        """GitHub 웹 GUI에서 해당 코드 라인으로 바로 이동하는 링크 생성"""
        sha = commit_sha if commit_sha else self.default_branch
        return f"https://github.com/{self.owner}/{self.repo}/blob/{sha}/{file_path}#L{line_number}"


# ---------------------------------------------------------------------------
# 4. 모듈 사용 예시 (테스트)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    TOKEN = "your_personal_access_token_here"
    OWNER = "my-org"
    REPO = "my-repo"

    client = GitHubEnricher(token=TOKEN, owner=OWNER, repo=REPO, default_branch="main")

    # 1. API 잔여량 확인
    print("API 잔여량:", client.get_rate_limit())

    # 2. 특정 파일 최신 커밋 조회
    file_path = "src/main/java/com/example/demo/security/JwtTokenProvider.java"
    last_commit = client.get_file_last_commit(file_path)
    print("\n[최근 파일 커밋]", last_commit)

    # 3. 특정 라인 Blame 추적 (33번 줄)
    blame_info = client.get_line_blame(file_path, line_number=33)
    print("\n[라인 Blame 추적]", blame_info)

    # 4. 장애 티켓 생성 / 중복 처리
    if blame_info:
        issue_result = client.report_issue_or_comment(
            title=f"[장애] JwtTokenProvider.java:33 {blame_info.author_name} 수정 건 예외 발생",
            body=(
                f"**발생 위치**: [{file_path}:{blame_info.line_number}]({blame_info.deep_link_url})\n"
                f"**마지막 수정 커밋**: {blame_info.commit_sha} ({blame_info.commit_message})\n"
                f"**담당 개발자**: {blame_info.author_name} ({blame_info.author_email})"
            ),
            labels=["bug", "was-error"],
        )
        print("\n[이슈 처리 결과]", issue_result)
