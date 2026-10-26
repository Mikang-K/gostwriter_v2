export async function api<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
  const response = await fetch(path, body === undefined ? { cache: "no-store" } : {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    // Full navigation clears private page state when the session expires.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    if (response.status === 401 && !path.endsWith("login")) window.location.assign("/auth");
    throw new Error(data.error?.code || "REQUEST_FAILED");
  }
  return data as T;
}

export function message(error: unknown): string {
  const code = error instanceof Error ? error.message : "REQUEST_FAILED";
  return ({ API_UNAVAILABLE: "API 서버가 실행 중인지 확인해 주세요.",
    DATABASE_NOT_CONFIGURED: "데이터베이스 설정이 필요합니다. 실행 문서를 확인해 주세요.",
    INVALID_PASSWORD: "암호를 다시 확인해 주세요.", PASSWORD_NOT_CONFIGURED: "서버의 로그인 암호를 먼저 설정해 주세요.",
    INVALID_INPUT: "입력 길이와 필수 항목을 확인해 주세요.", RATE_LIMITED: "요청이 많습니다. 잠시 후 다시 시도해 주세요.",
    REVISION_CONFLICT: "다른 작업으로 내용이 바뀌었습니다. 새로고침 후 다시 시도해 주세요.",
    GENERATION_ACTIVE: "이 작품의 생성이 진행 중입니다.", LOCAL_BUSY: "로컬 모델이 다른 작업을 처리 중입니다.",
    CONTEXT_BUDGET_EXCEEDED: "현재 모델에 전달할 설정이 너무 깁니다. 세계관·작가 노트를 줄여 주세요.",
    MODEL_MISSING: "로컬 모델이 설치되지 않았습니다. 실행 문서에서 모델 설치를 확인해 주세요.",
    UNAVAILABLE: "모델 서버에 연결할 수 없습니다.", INVALID_MEMORY: "본문의 상태 정보를 정리하지 못했습니다. 초안은 따로 보관됩니다.",
    TIMEOUT: "생성 시간이 초과되었습니다. 초안을 확인하고 다시 시도해 주세요.",
    STREAM_INTERRUPTED: "연결이 끊겼습니다. 저장된 생성 상태를 확인합니다.",
    CLOUD_NOT_CONFIGURED: "외부 모델은 서버에서 허용·설정한 경우에만 사용할 수 있습니다.",
    ALREADY_COMMITTED: "이미 저장이 완료되었습니다. 원고를 다시 확인해 주세요.",
    NOT_FOUND: "요청한 자료가 없습니다.", OUTPUT_LENGTH: "출력 길이 제한에 도달해 확정하지 않았습니다.",
  } as Record<string, string>)[code] || `작업을 완료하지 못했습니다. (${code})`;
}

export type Character = { id: string; name: string; gender: string; ageGroup: string;
  personality: string; speechStyle: string; background: string };
export type ProjectInput = { title: string; genre: string; language: "ko"|"en"|"ja"; notes: string;
  worldBible: string; styleExamples: string[]; targetEpisodes: number|null; characters: Character[] };
export type Episode = { id: string; episodeNumber: number; body: string|null; summary: string };
export type Snapshot = ProjectInput & { projectId: string; revision: number; archived: boolean;
  memory: { isFinished: boolean; plotState: string; nextOptions: string[]; characterStates: unknown[];
    openForeshadowing: unknown[] }; episodes: Episode[] };
