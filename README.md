# aws-cli-tools

`aws-cli-tools`는 AWS 계정 작업을 조금 더 빠르게 처리하기 위한 작은 Python CLI 도구입니다.  
현재는 임시 세션 토큰 발급, 여러 리전 반복 실행, EC2 인스턴스 조회, SSM 세션 시작 기능을 제공합니다.

## 무엇을 할 수 있나요?

- `login`: STS 임시 세션 토큰을 받아 `~/.aws/credentials`와 `~/.aws/config`를 갱신합니다.
- `region-loop`: 입력한 `aws ...` 명령을 모든 AWS 리전에 반복 실행합니다.
- `resolve-instance`: 인스턴스 ID, IP, Name 태그로 EC2 인스턴스를 찾아 리전과 메타데이터를 출력합니다.
- `ssm`: 대상을 찾아 해당 인스턴스로 AWS SSM 세션을 시작합니다.
- `version`: 현재 버전을 출력합니다.

## 준비 사항

이 프로젝트를 사용하기 전에 아래 항목이 준비되어 있어야 합니다.

- AWS 접근이 가능한 로컬 환경
- `~/.aws/credentials` 또는 `~/.aws/config`에 사용할 프로필이 설정되어 있어야 함
- `uv` 설치
- Python 사용 가능 환경
- `ssm` 명령을 사용할 경우 AWS CLI 설치 필요

## 설치

저장소를 받은 뒤 프로젝트 루트에서 의존성을 설치합니다.

```bash
uv sync
```

도움말은 아래 명령으로 확인할 수 있습니다.

```bash
uv run aws-cli-tools --help
```

## 빠른 시작

### 1. `.env-example`을 복사해 `.env` 설정하기

프로젝트는 시작할 때 `.env` 파일을 자동으로 읽습니다. 처음 사용할 때는 `.env-example`을 복사해서 `.env`를 먼저 만들어 두는 것을 권장합니다.

예시:

```bash
cp .env-example .env
```

`.env`에는 최소한 기본 소스 프로필명을 넣어두는 것이 좋습니다. MFA를 사용한다면 MFA ARN도 함께 설정하세요.

```env
AWS_SOURCE_PROFILE=example_source_profile
AWS_MFA_SERIAL=arn:aws:iam::123456789012:mfa/your-username
AWS_REGION_PRIORITY=ap-northeast-2,ap-northeast-1,us-west-2
```

`.env` 파일은 저장소에 커밋하지 않아야 하며, 현재 `.gitignore`에 포함되어 있습니다.

### 2. 로그인용 임시 세션 발급

예시:

```bash
uv run aws-cli-tools login --source-profile example_source_profile --target-profile default
```

동작 방식:

- `source_profile`로 AWS STS에 세션 토큰을 요청합니다.
- 발급된 임시 자격 증명을 `target_profile`에 기록합니다.
- 가능하면 `~/.aws/config`에서 원본 프로필 설정도 함께 복사합니다.
- MFA가 필요한 경우 토큰 코드를 묻거나, 옵션으로 직접 넘길 수 있습니다.

자주 쓰는 옵션:

- `--source-profile`: STS 인증에 사용할 원본 프로필
- `--target-profile`: 임시 자격 증명을 덮어쓸 대상 프로필
- `--duration`: 세션 유지 시간(기본 28800초, 8시간)
- `--mfa-serial`: MFA 장치 ARN
- `--token-code`: MFA 코드

예시:

```bash
uv run aws-cli-tools login \
  --source-profile example_source_profile \
  --target-profile default \
  --duration 3600 \
  --token-code 123456
```

`.env`에 `AWS_SOURCE_PROFILE`을 설정해 두면 `--source-profile` 옵션을 매번 넘기지 않아도 됩니다.

리전 조회 우선순위를 앞당기고 싶다면 `.env`에 `AWS_REGION_PRIORITY`를 넣을 수 있습니다. 예를 들어 `ap-northeast-2,ap-northeast-1,us-west-2`처럼 지정하면, 인스턴스 조회와 SSM 대상 로딩 시 이 순서대로 먼저 조회하고 나머지 리전은 뒤이어 조회합니다.

주의:
`target_profile=default`는 실제 로컬 AWS 기본 자격 증명을 덮어쓸 수 있습니다. 기존에 장기 자격 증명을 쓰고 있다면 특히 조심해서 사용해야 합니다.

### 3. 모든 리전에 같은 AWS CLI 명령 실행

```bash
uv run aws-cli-tools region-loop --profile default
```

실행하면 프롬프트가 나타나고, 아래처럼 실제 `aws` 명령을 입력합니다.

```text
aws ec2 describe-vpcs
```

이 명령은 다음 순서로 동작합니다.

- 사용 가능한 리전을 조회합니다.
- 첫 번째 리전을 기준으로 실행 예시를 보여줍니다.
- 전체 리전에 대해 실행할지 확인합니다.
- 각 리전마다 `aws --region <region> ...` 형식으로 명령을 실행합니다.

주의:
이 기능은 입력한 명령을 모든 리전에 실행하므로 조회성 명령부터 사용하는 것을 권장합니다.

### 4. EC2 인스턴스 위치 찾기

인스턴스 ID, 사설/공인 IP, Name 태그 중 하나로 조회할 수 있습니다.

```bash
uv run aws-cli-tools resolve-instance i-0123456789abcdef0
uv run aws-cli-tools resolve-instance 10.0.0.15
uv run aws-cli-tools resolve-instance my-app-web-01
```

동작 방식:

- 기본 프로필(`default`)로 계정에서 사용 가능한 리전을 조회합니다.
- 여러 리전을 병렬로 검색합니다.
- 결과가 하나면 인스턴스 정보와 리전을 출력합니다.
- 결과가 여러 개면 모호하다고 알려주고 후보 목록을 출력합니다.
- 단일 결과는 잠시 로컬 캐시에 저장해 다음 조회를 빠르게 합니다.

캐시를 무시하려면:

```bash
uv run aws-cli-tools resolve-instance my-app-web-01 --no-cache
```

### 5. 바로 SSM 접속하기

```bash
uv run aws-cli-tools ssm
uv run aws-cli-tools ssm i-0123456789abcdef0
uv run aws-cli-tools ssm 10.0.0.15
uv run aws-cli-tools ssm my-app-web-01
```

이 명령은 두 가지 방식으로 동작합니다.

- 인자를 생략하면: `Textual` 기반의 인터랙티브 테이블을 먼저 띄우고, 각 리전의 온라인 SSM 관리 대상 EC2 인스턴스가 조회되는 대로 목록이 계속 채워집니다.
- 인자를 주면: 먼저 대상을 조회한 뒤, 단일 매치면 바로 접속하고 여러 개가 나오면 같은 테이블 UI에서 방향키로 하나를 고르게 합니다.

조작 방법:

- `↑` / `↓`: 서버 이동
- `Enter`: 선택한 서버로 접속
- `q` / `Esc`: 종료

선택이 끝나면 아래와 비슷한 형식으로 SSM 세션을 시작합니다.

```bash
aws ssm start-session --target <instance-id> --region <region> --profile default
```

주의:

- 로컬에 `aws` CLI가 설치되어 있어야 합니다.
- 대상 인스턴스가 SSM 접속 가능한 상태여야 합니다.
- 현재 구현은 항상 `default` 프로필로 SSM 세션을 시작합니다.

### 6. 버전 확인

```bash
uv run aws-cli-tools version
```

## 자주 쓰는 명령 모음

```bash
uv sync
uv run aws-cli-tools --help
uv run aws-cli-tools login --help
uv run aws-cli-tools region-loop --help
uv run aws-cli-tools resolve-instance --help
uv run aws-cli-tools ssm --help
uv run aws-cli-tools version
```

## 파일에 어떤 영향이 있나요?

특히 `login` 명령은 아래 파일을 직접 읽거나 수정합니다.

- `~/.aws/credentials`
- `~/.aws/config`

따라서 처음 사용하기 전에는 기존 AWS 설정을 백업해 두는 것이 좋습니다.

`resolve-instance`는 아래 캐시 파일을 사용할 수 있습니다.

- `~/.cache/aws-cli-tools/resolve-instance.json`

## 문제 해결

### 엔트리포인트가 실행되지 않을 때

우선 아래 명령으로 실행해 보세요.

```bash
uv run python3 main.py --help
```

### AWS 인증 오류가 날 때

확인할 것:

- `source_profile` 또는 `default` 프로필이 실제로 존재하는지
- 자격 증명이 만료되지 않았는지
- EC2 조회 권한과 STS 권한이 있는지
- MFA가 필요하다면 `AWS_MFA_SERIAL`과 토큰 코드가 올바른지

### `ssm`이 실패할 때

확인할 것:

- AWS CLI가 설치되어 있는지
- 대상 인스턴스가 SSM Managed Instance인지
- 해당 리전과 인스턴스에 접근 권한이 있는지

## 개발 메모

- CLI 프레임워크: `typer`
- AWS SDK: `boto3`
- 환경 변수 로딩: `python-dotenv`
- 의존성 관리: `uv`

## 주의할 점

- 이 도구는 실제 사용자 AWS 설정 파일을 변경할 수 있습니다.
- `region-loop`는 입력한 명령을 모든 리전에 실행합니다.
- 운영 계정에서 사용한다면 먼저 읽기 전용 명령으로 검증하는 것을 권장합니다.
