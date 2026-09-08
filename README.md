# aws-cli-tools

`aws-cli-tools`는 AWS 계정 작업을 조금 더 빠르게 처리하기 위한 작은 Python CLI 도구입니다.  
현재는 임시 세션 토큰 발급, 여러 리전 반복 실행, EC2 인스턴스 조회 및 RI/SP 검토용 CSV 내보내기, SSM 세션 시작, SSM 대상 CSV 출력 기능을 제공합니다.

## 무엇을 할 수 있나요?

- `login`: STS 임시 세션 토큰을 받아 `~/.aws/credentials`와 `~/.aws/config`를 갱신합니다.
- `region-loop`: 입력한 `aws ...` 명령을 모든 AWS 리전에 반복 실행합니다.
- `resolve-instance`: 인스턴스 ID, IP, Name 태그로 EC2 인스턴스를 찾아 리전과 메타데이터를 출력합니다.
- `inventory ec2`: 활성화된 모든 리전의 EC2 인스턴스를 조회해 RI/SP 검토용 CSV를 `~/Downloads/` 하위에 저장합니다.
- `inventory commitment`: RI/Savings Plans 적용 대상 서비스 유형과 실제 리전별 리소스를 CSV로 저장합니다.
- `inventory ri`: 활성화된 모든 리전의 EC2 Reserved Instance 약정을 조회해 CSV로 저장합니다.
- `inventory sp`: 계정의 Savings Plan 계약 현황을 조회해 적용 리전과 함께 CSV로 저장합니다.
- `ssm`: 대상을 찾아 해당 인스턴스로 AWS SSM 세션을 시작합니다.
- `ssm-targets`: `ssm` 브라우저에 보이는 온라인 SSM 대상 목록을 CSV로 출력합니다.
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
- MFA가 필요한 경우, 먼저 Rich 스타일의 안내 박스를 보여준 뒤 OTP를 입력받습니다.
- `--token-code`를 직접 넘기면 별도 프롬프트 없이 바로 진행합니다.

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
uv run aws-cli-tools ssm --no-cache
uv run aws-cli-tools ssm i-0123456789abcdef0
uv run aws-cli-tools ssm 10.0.0.15
uv run aws-cli-tools ssm my-app-web-01
```

이 명령은 두 가지 방식으로 동작합니다.

- 인자를 생략하면: `Textual` 기반의 인터랙티브 테이블을 먼저 띄우고, 최근 5분 이내의 리전별 SSM 대상 캐시가 있으면 먼저 보여준 뒤 각 리전의 온라인 SSM 관리 대상 EC2 인스턴스가 백그라운드에서 다시 조회되는 대로 목록을 최신 상태로 교체합니다.
- 인자를 주면: 먼저 대상을 조회한 뒤, 단일 매치면 바로 접속하고 여러 개가 나오면 같은 테이블 UI에서 방향키로 하나를 고르게 합니다.
- SSM 대상 로딩 중 자격 증명이 만료되어 `RequestExpired`가 발생하면, `login`을 한 번 자동 실행한 뒤 같은 흐름을 다시 시도합니다.
- `--no-cache`를 주면: `resolve-instance` 캐시와 SSM 브라우저 캐시를 모두 무시하고 항상 새로 조회합니다.

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

### 6. SSM 대상 목록을 CSV로 보기

```bash
uv run aws-cli-tools ssm-targets
uv run aws-cli-tools ssm-targets --no-cache
```

이 명령은 `ssm`에서 인터랙티브 브라우저가 보여주는 것과 같은 온라인 SSM 관리 대상 EC2 목록을 CSV로 터미널에 출력합니다.

- 기본적으로 최근 5분 이내의 리전별 SSM 대상 캐시를 먼저 사용합니다.
- `--no-cache`를 주면 SSM 브라우저 캐시를 무시하고 항상 새로 조회합니다.
- 자격 증명이 만료되어 `RequestExpired`가 발생하면 `login`을 한 번 자동 실행한 뒤 다시 시도합니다.
- 출력 컬럼은 `region,name,instance_id,private_ip,public_ip,state` 순서입니다.

예시 출력:

```csv
region,name,instance_id,private_ip,public_ip,state
ap-northeast-2,example-instance,i-0123456789abcdef0,10.0.0.12,-,running
```

### 7. RI/SP 검토용 EC2 인벤토리 CSV 저장

```bash
uv run aws-cli-tools inventory ec2
```

이 명령은 기본 프로필로 계정에서 활성화된 모든 리전의 EC2 인스턴스를 읽고, 아래 위치에 실행 시각이 붙은 CSV 파일을 생성합니다.

```text
~/Downloads/aws-cli-tools/ec2-inventory/ec2-instances-<UTC timestamp>.csv
```

저장 폴더를 직접 지정할 수도 있습니다.

```bash
uv run aws-cli-tools inventory ec2 --output-dir ~/Downloads/ri-sp-review
```

CSV에는 다음과 같은 구매 검토용 정보가 포함됩니다.

- 리전, Availability Zone, 인스턴스 ID, Name 태그
- AMI, 인스턴스 타입, 상태, 플랫폼/플랫폼 상세, 아키텍처, 테넌시
- Spot 여부, 시작 시각, Usage Operation, vCPU 코어/스레드 정보
- 하이퍼바이저, 가상화 방식, EBS 최적화 여부, VPC/Subnet/IP
- 예약 ID, 계정 소유자 ID, 전체 태그 JSON

한 리전이라도 조회에 실패하면 불완전한 CSV를 저장하지 않습니다. 연결이 불안정한 경우 `--connect-timeout`, `--read-timeout`, `--max-attempts` 옵션을 조정할 수 있습니다.

이 파일은 현재 EC2 인벤토리 원본입니다. RI/SP 구매 수량과 금액은 실제 On-Demand 사용량, 기존 RI/SP 적용 여부, 사용률과 함께 Cost Explorer 또는 CUR에서 추가로 확인해야 합니다. 특히 Spot 사용량은 Savings Plans 적용 대상이 아니므로 `spot_instance` 컬럼을 별도로 확인하세요.

### 8. 기존 RI 약정 CSV 저장

```bash
uv run aws-cli-tools inventory ri
```

이 명령은 기본 프로필로 모든 활성화 리전의 EC2 Reserved Instance 약정을 조회하고 아래 위치에 실행 시각이 붙은 CSV를 생성합니다.

```text
~/Downloads/aws-cli-tools/ri-inventory/ri-commitments-<UTC timestamp>.csv
```

저장 폴더를 직접 지정할 수도 있습니다.

```bash
uv run aws-cli-tools inventory ri \
  --output-dir ~/Downloads/ri-sp-review
```

CSV에는 리전, RI ID, 인스턴스 타입/수량, 상태, scope, Availability Zone, 테넌시, 플랫폼, Standard/Convertible, 결제 방식, 약정 기간, 시작/종료 시각, 고정 가격, 시간당 사용 가격, 반복 비용, 태그가 포함됩니다. `state` 컬럼으로 `active`, `payment-pending`, `payment-failed`, `retired` 등 AWS 상태를 구분할 수 있습니다.

한 리전이라도 조회에 실패하면 불완전한 CSV를 저장하지 않습니다. 이 파일은 현재 보유 약정의 현황 원본이며, 새 RI 구매 수량은 EC2 사용량·기존 RI 적용률·SP 사용량과 함께 판단해야 합니다.

### 9. Savings Plan 계약 CSV 저장

```bash
uv run aws-cli-tools inventory sp
```

이 명령은 Savings Plans API에서 계정의 계약 목록을 조회하고 아래 위치에 실행 시각이 붙은 CSV를 생성합니다.

```text
~/Downloads/aws-cli-tools/sp-inventory/sp-commitments-<UTC timestamp>.csv
```

저장 폴더를 직접 지정할 수도 있습니다.

```bash
uv run aws-cli-tools inventory sp \
  --output-dir ~/Downloads/ri-sp-review
```

CSV에는 Savings Plan ID/ARN, `state`, Savings Plan 유형, 적용 대상 `region`, EC2 인스턴스 패밀리, 상품 유형, 결제 옵션, 통화, 시간당 약정 금액, 선불/반복 결제 금액, 약정 기간, 시작/종료/반환 가능 시각, 설명과 태그가 포함됩니다. 금액은 AWS API가 반환한 문자열을 그대로 저장해 정밀도를 보존합니다.

Savings Plans 계약 조회는 EC2 활성화 리전을 하나씩 호출하지 않고 계정 단위 API에서 한 번에 수행합니다. 따라서 CSV의 `region`은 각 계약에 포함된 적용 대상 리전이며, Compute Savings Plan처럼 특정 리전에 한정되지 않는 계약은 AWS가 반환하는 값이 비어 있을 수 있습니다.

`state`는 현재 계약 상태를 보여주는 원본 값입니다. 신규 RI/SP 구매 판단에는 이 CSV만 사용하지 말고 Cost Explorer의 Savings Plans 사용률·커버리지·추천 결과와 실제 On-Demand 사용량을 함께 확인하세요.

### 10. RI/SP 적용 대상 리소스와 서비스 유형 CSV 저장

```bash
uv run aws-cli-tools inventory commitment
```

이 명령은 AWS 공식 적용 범위를 기준으로 다음 두 개의 CSV를 생성합니다.

```text
~/Downloads/aws-cli-tools/commitment-inventory/commitment-eligibility-<UTC timestamp>.csv
~/Downloads/aws-cli-tools/commitment-inventory/commitment-resources-<UTC timestamp>.csv
```

`commitment-eligibility`에는 다음 유형이 포함됩니다.

- Compute Savings Plan: EC2, ECS/EKS Fargate, Lambda
- EC2 Instance Savings Plan: EC2
- SageMaker AI Savings Plan: SageMaker AI
- Database Savings Plan: RDS/Aurora, Aurora DSQL, DynamoDB, ElastiCache for Valkey, DocumentDB, Timestream, Neptune/Neptune Analytics, Keyspaces, DMS, OpenSearch managed domains/Serverless
- Reserved Instance/Node/Capacity: EC2, RDS, ElastiCache, OpenSearch, Redshift, MemoryDB, DynamoDB

`commitment-resources`에는 각 활성화 리전의 실제 리소스와 인스턴스 타입, 노드 수, 용량, 상태, 적용 가능한 약정 유형이 포함됩니다. API 권한이 없거나 해당 리전에서 서비스를 사용할 수 없는 경우에도 `collection_status`, `error_code`, `error_message` 행을 남겨 누락 원인을 확인할 수 있습니다.

Savings Plans는 실제 사용량에 적용되는 약정이므로 Lambda 함수, Fargate 프로파일/태스크, SageMaker 리소스 목록만으로 구매 수량을 결정할 수 없습니다. Spot/Fargate Spot 사용량은 대상에서 제외하고, 최종 구매 판단은 Cost Explorer의 RI/SP 사용률·커버리지·구매 추천 및 CUR 사용량과 함께 검토하세요. 자세한 적용 범위는 [Savings Plans types](https://docs.aws.amazon.com/savingsplans/latest/userguide/plan-types.html), [Database Savings Plans pricing](https://aws.amazon.com/savingsplans/database-pricing/), [RI recommendations](https://docs.aws.amazon.com/cost-management/latest/userguide/ri-recommendations.html)를 참고하세요.

### 11. 버전 확인

```bash
uv run aws-cli-tools version
```

## 자주 쓰는 명령 모음

```bash
uv sync
uv run pytest tests/
uv run pytest --cov aws_cli_tools tests/
uv run aws-cli-tools --help
uv run aws-cli-tools login --help
uv run aws-cli-tools region-loop --help
uv run aws-cli-tools resolve-instance --help
uv run aws-cli-tools inventory commitment --help
uv run aws-cli-tools ssm --help
uv run aws-cli-tools ssm-targets --help
uv run aws-cli-tools inventory --help
uv run aws-cli-tools inventory ec2 --help
uv run aws-cli-tools inventory ri --help
uv run aws-cli-tools inventory sp --help
uv run aws-cli-tools version
```

## 파일에 어떤 영향이 있나요?

특히 `login` 명령은 아래 파일을 직접 읽거나 수정합니다.

- `~/.aws/credentials`
- `~/.aws/config`

따라서 처음 사용하기 전에는 기존 AWS 설정을 백업해 두는 것이 좋습니다.

`resolve-instance`는 아래 캐시 파일을 사용할 수 있습니다.

- `~/.cache/aws-cli-tools/resolve-instance.json`
- `~/.cache/aws-cli-tools/ssm-targets.json`

`ec2-inventory`는 아래 폴더에 CSV 파일을 생성합니다.

- `~/Downloads/aws-cli-tools/ec2-inventory/`

`ri-inventory`는 아래 폴더에 CSV 파일을 생성합니다.

- `~/Downloads/aws-cli-tools/ri-inventory/`

`sp-inventory`는 아래 폴더에 CSV 파일을 생성합니다.

- `~/Downloads/aws-cli-tools/sp-inventory/`

`commitment-inventory`는 아래 폴더에 적용 범위와 리소스 CSV를 생성합니다.

- `~/Downloads/aws-cli-tools/commitment-inventory/`

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
- 자격 증명 만료로 `RequestExpired`가 발생했다면, 자동 `login` 재시도가 실패했는지 함께 확인할 것

### 버전 정보

현재 문서 기준 최신 애플리케이션 버전은 `0.9.0`입니다.

## 개발 메모

- CLI 프레임워크: `typer`
- AWS SDK: `boto3`
- 환경 변수 로딩: `python-dotenv`
- 의존성 관리: `uv`
- 테스트 커버리지 확인: `pytest-cov`

## 주의할 점

- 이 도구는 실제 사용자 AWS 설정 파일을 변경할 수 있습니다.
- `region-loop`는 입력한 명령을 모든 리전에 실행합니다.
- 운영 계정에서 사용한다면 먼저 읽기 전용 명령으로 검증하는 것을 권장합니다.
