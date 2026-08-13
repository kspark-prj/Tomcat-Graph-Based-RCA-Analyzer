import datetime
import os
import random

OUTPUT_FILENAME = "wildfly_server.log"
TOTAL_RECORDS = 100000

# WildFly 정상 로그 템플릿
LOG_TEMPLATES = [
    ("INFO", "default task-{exec_id}", "org.wildfly.extension.undertow", "JBWEB001083: Undertow HTTP listener default listening on 0.0.0.0:8080"),
    ("INFO", "default task-{exec_id}", "com.example.demo.controller.OrderController", "Completed 200 OK for request [/api/v1/orders]"),
    ("DEBUG", "default task-{exec_id}", "org.jboss.as.jpa", "Starting JPA transaction for EntityManager"),
    ("WARN", "ServerService Thread Pool -- {exec_id}", "org.jboss.as.dependency.private", "WFLYSRV0018: Deployment delay detected"),
]

# 장애 시나리오 모음 (동일 4대 장애)
ERROR_SCENARIOS = [
    {
        "logger": "org.hibernate.engine.jdbc.spi.SqlExceptionHelper",
        "type": "org.springframework.dao.CannotAcquireLockException",
        "msg": "Could not open JPA EntityManager for transaction; nested exception is org.hibernate.exception.JDBCConnectionException",
        "stack": [("com.example.demo.repository.UserRepository", "findById", 45), ("com.example.demo.service.UserService", "getUserDetails", 112)],
        "caused_by": {
            "type": "com.zaxxer.hikari.pool.HikariPool$PoolInitializationException",
            "msg": "Connection is not available, request timed out after 30000ms.",
        },
    },
    {
        "logger": "com.example.demo.service.PaymentService",
        "type": "org.springframework.web.client.ResourceAccessException",
        "msg": 'I/O error on POST request for "https://api.external-pg.com/v1/payments": Read timed out',
        "stack": [("com.example.demo.service.PaymentService", "processPayment", 146)],
        "caused_by": {"type": "java.net.SocketTimeoutException", "msg": "Read timed out"},
    },
    {
        "logger": "com.example.demo.security.JwtAuthenticationFilter",
        "type": "io.jsonwebtoken.ExpiredJwtException",
        "msg": "JWT expired at 2026-08-13T10:00:00Z. Current time: 2026-08-13T10:30:00Z",
        "stack": [("com.example.demo.security.JwtTokenProvider", "validateToken", 67)],
        "caused_by": None,
    },
    {
        "logger": "com.example.demo.service.NotificationService",
        "type": "java.lang.NullPointerException",
        "msg": 'Cannot invoke "com.example.demo.domain.User.getEmail()" because "user" is null',
        "stack": [("com.example.demo.service.NotificationService", "sendEmailNotice", 52)],
        "caused_by": None,
    },
]


def generate_wildfly_logs():
    print(f"🚀 WildFly server.log 테스트 로그 생성 시작: {OUTPUT_FILENAME}")
    current_time = datetime.datetime(2026, 8, 13, 9, 0, 0)

    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        for _ in range(TOTAL_RECORDS):
            current_time += datetime.timedelta(milliseconds=random.randint(50, 500))
            # WildFly 타임스탬프 포맷 (밀리초 구분자 쉼표 사용): 2026-08-13 09:00:00,123
            timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S,") + f"{random.randint(100, 999)}"
            exec_id = random.randint(1, 20)

            if random.random() < 0.12:
                scenario = random.choice(ERROR_SCENARIOS)
                f.write(f"{timestamp_str} ERROR [{scenario['logger']}] (default task-{exec_id}) {scenario['type']}: {scenario['msg']}\n")
                f.writelines(f"\tat {cls}.{method}({cls.split('.')[-1]}.java:{line_num})\n" for cls, method, line_num in scenario["stack"])
                if scenario["caused_by"]:
                    f.write(f"Caused by: {scenario['caused_by']['type']}: {scenario['caused_by']['msg']}\n")
            else:
                level, thread_tmpl, logger, msg = random.choice(LOG_TEMPLATES)
                thread = thread_tmpl.format(exec_id=exec_id)
                f.write(f"{timestamp_str} {level:<5} [{logger}] ({thread}) {msg}\n")

    print(f"✅ WildFly 로그 생성 완료: {OUTPUT_FILENAME}")


if __name__ == "__main__":
    generate_wildfly_logs()
