import os
import sys
import subprocess
import shutil

# Windows 콘솔 환경을 고려하여 stdout의 인코딩을 UTF-8로 변경합니다.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def run_cmd(args, cwd):
    return subprocess.run(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    )

def setup():
    # 1. 톰캣 테스트 로그 파일 생성
    print("1. 톰캣 테스트 로그 생성 중...")
    try:
        from generate_log.generate_tomcat_log import generate_tomcat_logs
        generate_tomcat_logs()
    except ImportError:
        print("Error: generate_log.generate_tomcat_log 모듈을 찾을 수 없습니다. 경로를 확인해 주세요.")
        return
    
    # 2. test_git_repo 폴더 생성 및 초기화
    repo_path = os.path.abspath("test_git_repo")
    print(f"2. 테스트용 임시 Git 저장소 생성 중: {repo_path}")
    if os.path.exists(repo_path):
        def on_rm_error(func, path, exc_info):
            import stat
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
            except Exception as e:
                print(f"Failed to delete {path}: {e}")
        shutil.rmtree(repo_path, onerror=on_rm_error)
        
    os.makedirs(repo_path, exist_ok=True)
    
    # Git init
    run_cmd(["git", "init"], repo_path)
    run_cmd(["git", "config", "user.name", "Test User"], repo_path)
    run_cmd(["git", "config", "user.email", "test@example.com"], repo_path)
    
    # 3. 더미 소스 코드 구조 정의 및 다중 커밋 생성
    classes_info = [
        ("com/example/demo/repository/UserRepository.java", [
            "package com.example.demo.repository;\npublic class UserRepository {\n    public void findById(int id) {\n        // Initial commit\n    }\n}",
            "package com.example.demo.repository;\npublic class UserRepository {\n    public void findById(int id) {\n        // Version 2: Added some logic\n        System.out.println(\"Finding user: \" + id);\n    }\n}",
            "package com.example.demo.repository;\npublic class UserRepository {\n    public void findById(int id) {\n        // Version 3: Potential Exception source\n        if (id < 0) throw new IllegalArgumentException();\n    }\n}"
        ]),
        ("com/example/demo/service/UserService.java", [
            "package com.example.demo.service;\npublic class UserService {\n    public void getUserDetails(int id) {\n        // Initial user service logic\n    }\n}",
            "package com.example.demo.service;\npublic class UserService {\n    public void getUserDetails(int id) {\n        // Version 2: Fetch details\n        System.out.println(\"Getting details for: \" + id);\n    }\n}"
        ]),
        ("com/example/demo/service/NotificationService.java", [
            "package com.example.demo.service;\npublic class NotificationService {\n    public void sendEmailNotice() {\n        // Initial Notification logic\n    }\n}",
            "package com.example.demo.service;\npublic class NotificationService {\n    public void sendEmailNotice() {\n        // Bug: User object can be null\n        Object user = null;\n        user.toString();\n    }\n}"
        ]),
        ("com/example/demo/service/PaymentService.java", [
            "package com.example.demo.service;\npublic class PaymentService {\n    public void processPayment() {\n        // Payment logic\n    }\n}"
        ]),
        ("com/example/demo/controller/OrderController.java", [
            "package com.example.demo.controller;\npublic class OrderController {\n    public void createOrder() {\n        // Order creation\n    }\n}"
        ])
    ]
    
    for rel_path, contents in classes_info:
        full_path = os.path.join(repo_path, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        # 순차적으로 내용을 갱신하며 커밋을 남김
        for idx, content in enumerate(contents):
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            run_cmd(["git", "add", "."], repo_path)
            commit_msg = f"Commit {idx+1} for {os.path.basename(rel_path)}"
            run_cmd(["git", "commit", "-m", commit_msg], repo_path)
            
    print("\n✅ 테스트 환경 구성 완료!")
    print(f"- 생성된 로그 파일: {os.path.abspath('catalina_test.log')}")
    print(f"- 생성된 Git 저장소 경로: {repo_path}")
    print("\n[테스트 방법]")
    print("1. 'uv run python main.py'로 프로그램을 구동합니다.")
    print("2. [📁 통합 로그 파일 선택 및 자동 분석 시작] 버튼을 눌러 생성된 'catalina_test.log'를 분석합니다.")
    print("3. 분석 완료 후 하단의 '📁 Git 로컬 경로'에 아래의 경로를 붙여넣습니다:")
    print(f"   {repo_path}")
    print("4. 분석 체인 트리의 노드를 클릭해 Git History 다이어로그를 띄우고 커밋을 더블클릭합니다.")

if __name__ == "__main__":
    setup()
