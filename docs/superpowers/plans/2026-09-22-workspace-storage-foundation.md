# Workspace Storage Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the trusted Workspace boundary that rejects path and ZIP escapes, serializes one application instance, probes storage guarantees, and feeds verified SQLite/Artifact behavior.

**Architecture:** Introduce a small `WorkspacePaths` capability object as the only component that canonicalizes Workspace-relative paths, plus focused ZIP and process-lock helpers. A `probe_workspace()` service performs destructive-but-self-cleaning startup probes inside a private temporary directory and returns the effective SQLite journal mode; existing database and Artifact code consume those verified results instead of duplicating path assumptions.

**Tech Stack:** Python 3.12 standard library (`pathlib`, `zipfile`, `sqlite3`, `os`, `shutil`, `ctypes`/`msvcrt` on Windows and `fcntl` on POSIX), SQLAlchemy 2, pytest.

**Spec:** `docs/specs/07-local-security-and-storage.md` — this plan implements only “文件与路径安全”, “数据目录能力探测”, the single-instance locking prerequisite, and the related SQLite configuration. HTTP authentication, parser resource limits, provider disclosure, untrusted-content prompting, and backup/restore remain separate modules.

## Global Constraints

- Keep the Python package in `src/ppt_agent`; do not introduce a root-level package.
- Develop on the current `feat/ppt` branch; do not create a worktree.
- Use only Python 3.12 and current project dependencies; add no third-party locking or filesystem package.
- Reject known network filesystems for the active Workspace; warn for recognized sync directories; reject unverified required capabilities in production mode.
- Permit `DELETE` journal fallback only when atomic rename, exclusive locking, file/directory sync, and local storage are verified.
- Validate a file target before opening and after writing/renaming; client-facing code receives IDs or Workspace-relative paths, never arbitrary absolute paths.
- Preserve the repository rule of one functional commit for this complete module; intermediate TDD tasks remain uncommitted until final verification and review.

## Review Focus

- Windows drive-relative, UNC, rooted-backslash, mixed-separator, and case-normalization inputs must never bypass containment checks.
- A symlink or junction introduced after initial path validation must be caught by the post-write/post-rename verification path.
- A second process must fail to acquire the Workspace lock without corrupting or releasing the first process's lock.
- WAL refusal must select `DELETE` only when every mandatory local filesystem capability still passes; network or unknown storage must fail closed in production.
- ZIP entries using backslashes, dot/parent components, drive prefixes, UNC names, or Unix-mode symlink metadata must be rejected before extraction.

---

### Task 1: Canonical Workspace paths and safe ZIP member validation

**Files:**
- Create: `src/ppt_agent/storage/workspace.py`
- Create: `src/ppt_agent/storage/zip_safety.py`
- Create: `tests/unit/storage/test_workspace.py`
- Create: `tests/unit/storage/test_zip_safety.py`
- Modify: `src/ppt_agent/storage/__init__.py`

**Interfaces:**
- Consumes: `pathlib.Path`, `zipfile.ZipInfo`.
- Produces: `WorkspacePaths(root: Path, *, create: bool = False)`, `WorkspacePaths.resolve(relative: str | Path, *, must_exist: bool = False) -> Path`, `WorkspacePaths.verify(path: Path, *, must_exist: bool = True) -> Path`, `UnsafeWorkspacePath`, and `validate_zip_members(members: Iterable[ZipInfo]) -> tuple[SafeZipMember, ...]`.

- [ ] **Step 1: Write failing Workspace path tests**

  Add table-driven tests proving ordinary nested relative paths resolve below the canonical root and that empty paths, `.`, `..`, existing symlink/junction escapes, absolute paths, UNC paths, drive-qualified paths, and mixed Windows separators raise `UnsafeWorkspacePath`. Add a mutation test that resolves a missing write target, replaces its validated parent with a symlink/junction to an outside directory, and proves `verify()` rejects the resulting target.

  ```python
  @pytest.mark.parametrize(
      "candidate",
      ("", ".", "../outside.txt", r"..\outside.txt", "/tmp/outside", r"C:\outside", r"\\server\share\x"),
  )
  def test_workspace_paths_reject_non_relative_or_escaping_names(
      tmp_path: Path, candidate: str
  ) -> None:
      paths = WorkspacePaths(tmp_path / "workspace", create=True)
      with pytest.raises(UnsafeWorkspacePath):
          paths.resolve(candidate)
  ```

- [ ] **Step 2: Run the Workspace path tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_workspace.py -q --basetemp='.cache/pytest-workspace-red'`

  Expected: collection fails because `ppt_agent.storage.workspace` does not exist.

- [ ] **Step 3: Implement the canonical path boundary**

  Implement `WorkspacePaths` so its constructor canonicalizes an existing root (creating it only when requested), rejects a non-directory root, and stores the canonical absolute root. `resolve()` must reject lexical absolute/rooted/drive/UNC forms using both `PurePosixPath` and `PureWindowsPath`, normalize both slash styles into components, reject empty, dot, and parent components, resolve existing ancestors (thereby following symlinks and junctions), and require the result to be beneath the canonical root. `verify()` must call `Path.resolve(strict=must_exist)`, require containment, and when `must_exist=True` require an actual file or directory. Export only the public types from `storage/__init__.py`.

  ```python
  class UnsafeWorkspacePath(ValueError):
      """A path is not a safe member of the active Workspace."""


  @dataclass(frozen=True, slots=True)
  class WorkspacePaths:
      root: Path

      def __init__(self, root: Path, *, create: bool = False) -> None: ...
      def resolve(self, relative: str | Path, *, must_exist: bool = False) -> Path: ...
      def verify(self, path: Path, *, must_exist: bool = True) -> Path: ...
  ```

- [ ] **Step 4: Run the Workspace path tests and verify GREEN**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_workspace.py -q --basetemp='.cache/pytest-workspace-green'`

  Expected: all tests pass, with symlink/junction cases skipped only when the current OS denies creating the test link.

- [ ] **Step 5: Write failing safe-ZIP tests**

  Test normal directory/file entries and reject `/absolute`, `../parent`, backslash traversal, `C:/drive`, `C:\drive`, UNC, NUL-containing names, duplicate normalized names, and a `ZipInfo` whose Unix external mode is `stat.S_IFLNK`. Assertions must inspect the normalized `SafeZipMember.path` literal rather than restating the validator algorithm.

  ```python
  def test_validate_zip_members_rejects_unix_symlink() -> None:
      member = ZipInfo("linked-file")
      member.create_system = 3
      member.external_attr = (stat.S_IFLNK | 0o777) << 16
      with pytest.raises(UnsafeZipEntry):
          validate_zip_members([member])
  ```

- [ ] **Step 6: Run safe-ZIP tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_zip_safety.py -q --basetemp='.cache/pytest-zip-red'`

  Expected: collection fails because `ppt_agent.storage.zip_safety` does not exist.

- [ ] **Step 7: Implement ZIP metadata validation**

  Add immutable `SafeZipMember(info: ZipInfo, path: PurePosixPath, is_directory: bool)` and `UnsafeZipEntry`. Normalize backslashes to `/`, validate with both POSIX and Windows path semantics, reject empty/dot/parent components and duplicate normalized destinations, and reject symlinks from Unix mode bits. Do not extract files in this module; later upload code must consume the validated tuple.

  ```python
  @dataclass(frozen=True, slots=True)
  class SafeZipMember:
      info: ZipInfo
      path: PurePosixPath
      is_directory: bool


  def validate_zip_members(
      members: Iterable[ZipInfo],
  ) -> tuple[SafeZipMember, ...]: ...
  ```

- [ ] **Step 8: Run Task 1 tests**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_workspace.py tests/unit/storage/test_zip_safety.py -q --basetemp='.cache/pytest-paths'`

  Expected: all Task 1 tests pass.

### Task 2: Cross-platform single-instance Workspace lock

**Files:**
- Create: `src/ppt_agent/storage/locking.py`
- Create: `tests/unit/storage/test_locking.py`
- Modify: `src/ppt_agent/storage/__init__.py`

**Interfaces:**
- Consumes: a canonical `WorkspacePaths` from Task 1.
- Produces: `WorkspaceInstanceLock(paths: WorkspacePaths, name: str = ".ppt-agent.lock")`, `acquire() -> None`, `release() -> None`, context-manager methods, `WorkspaceLockUnavailable`, and the private platform adapters `_try_lock(file) -> bool` / `_unlock(file) -> None`.

- [ ] **Step 1: Write failing lock lifecycle and contention tests**

  Test acquire/release/reacquire in one process, idempotence errors for double acquire/release, and metadata containing the current PID only after the lock is held. Use a spawned Python subprocess against a real temporary Workspace: while the parent holds the lock the child exits with a distinct contention code; after release a fresh child acquires successfully. This proves OS locking rather than an in-memory registry.

  ```python
  def test_workspace_lock_excludes_another_process(tmp_path: Path) -> None:
      paths = WorkspacePaths(tmp_path / "workspace", create=True)
      with WorkspaceInstanceLock(paths):
          blocked = run_lock_probe(paths.root)
      acquired = run_lock_probe(paths.root)
      assert blocked.returncode == 23
      assert acquired.returncode == 0
  ```

- [ ] **Step 2: Run lock tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_locking.py -q --basetemp='.cache/pytest-lock-red'`

  Expected: collection fails because `ppt_agent.storage.locking` does not exist.

- [ ] **Step 3: Implement the OS-backed lock**

  Open the lock file in binary read/write mode, ensure one lockable byte exists, then use non-blocking `msvcrt.locking(..., LK_NBLCK, 1)` on Windows or `fcntl.flock(..., LOCK_EX | LOCK_NB)` on POSIX. Keep the descriptor open for the entire lease. Only after acquiring, truncate and write UTF-8 JSON with PID and acquisition timestamp, flush, and `fsync`. On any failure, close the descriptor without unlinking a possibly active lock file. `release()` unlocks then closes; context exit always releases.

  ```python
  class WorkspaceLockUnavailable(RuntimeError):
      pass


  class WorkspaceInstanceLock:
      def acquire(self) -> None: ...
      def release(self) -> None: ...
      def __enter__(self) -> "WorkspaceInstanceLock": ...
      def __exit__(self, *exc_info: object) -> None: ...
  ```

- [ ] **Step 4: Run lock tests and verify GREEN**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_locking.py -q --basetemp='.cache/pytest-lock-green'`

  Expected: all lifecycle and real subprocess contention tests pass.

### Task 3: Storage capability probing and effective SQLite configuration

**Files:**
- Create: `src/ppt_agent/storage/capabilities.py`
- Create: `tests/unit/storage/test_capabilities.py`
- Modify: `src/ppt_agent/storage/database.py`
- Modify: `tests/unit/storage/test_database.py`
- Modify: `src/ppt_agent/storage/__init__.py`

**Interfaces:**
- Consumes: `WorkspacePaths`, `WorkspaceInstanceLock`, and `DatabaseConfig`.
- Produces: `FilesystemKind` (`LOCAL`, `NETWORK`, `SYNC`, `UNKNOWN`), immutable `WorkspaceCapabilities`, `WorkspaceCapabilityError`, `probe_workspace(root: Path, *, production: bool = True, minimum_free_bytes: int = 1_073_741_824, classifier: FilesystemClassifier | None = None) -> WorkspaceCapabilities`, and `WorkspaceCapabilities.database_config(path: Path, *, busy_timeout_ms: int = 5_000, wal_autocheckpoint: int = 1_000) -> DatabaseConfig`.

- [ ] **Step 1: Write failing probe-policy tests around real local I/O**

  On a real temporary directory, assert the probe verifies atomic `os.replace`, exclusive locking, file `fsync`, directory `fsync` where supported, free space, same-device staging/final directories, and a returned journal mode of `WAL` or `DELETE`; assert its private probe directory is removed on success and injected failure. Inject only the filesystem classifier and low-level WAL probe to deterministically cover: network rejection, sync warning, unknown rejection in production, unknown allowance in non-production, WAL success, safe `DELETE` fallback, insufficient space, and failure of rename/lock/fsync.

  ```python
  def test_probe_rejects_known_network_storage(tmp_path: Path) -> None:
      with pytest.raises(WorkspaceCapabilityError, match="network"):
          probe_workspace(
              tmp_path / "workspace",
              classifier=lambda _: FilesystemKind.NETWORK,
          )
  ```

- [ ] **Step 2: Run capability tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_capabilities.py -q --basetemp='.cache/pytest-capabilities-red'`

  Expected: collection fails because `ppt_agent.storage.capabilities` does not exist.

- [ ] **Step 3: Implement filesystem classification and probes**

  Classify UNC and Windows `GetDriveTypeW(...)=DRIVE_REMOTE` as `NETWORK`; inspect mounted filesystem types on POSIX for known network types; classify common OneDrive/Dropbox/iCloud path components as `SYNC`; return `UNKNOWN` when classification cannot be established. Inside a UUID-named directory below the Workspace, verify write/flush/file-fsync, same-directory atomic replacement with literal content comparison, directory fsync (mark unsupported on Windows without treating it as success), free bytes via `shutil.disk_usage`, same `st_dev` for staging/final, and real lock contention. Probe WAL using a temporary `sqlite3` database and read the actual `PRAGMA journal_mode=WAL` result. Always close handles and remove only the exact canonical probe directory.

  ```python
  @dataclass(frozen=True, slots=True)
  class WorkspaceCapabilities:
      root: Path
      filesystem_kind: FilesystemKind
      atomic_replace: bool
      exclusive_lock: bool
      file_fsync: bool
      directory_fsync: bool | None
      same_device: bool
      free_bytes: int
      journal_mode: JournalMode
      warnings: tuple[str, ...]

      def database_config(
          self,
          path: Path,
          *,
          busy_timeout_ms: int = 5_000,
          wal_autocheckpoint: int = 1_000,
      ) -> DatabaseConfig: ...
  ```

- [ ] **Step 4: Run capability tests and verify GREEN**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_capabilities.py -q --basetemp='.cache/pytest-capabilities-green'`

  Expected: all policy branches pass; the real local probe passes on Windows and POSIX.

- [ ] **Step 5: Write failing SQLite effective-setting tests**

  Extend `DatabaseConfig` tests to reject negative `wal_autocheckpoint`, configure a non-default value, and read back `PRAGMA wal_autocheckpoint`. Add a test creating the config through `WorkspaceCapabilities.database_config()` and assert the capability-selected `DELETE` mode is what SQLite actually reports.

  ```python
  def test_sqlite_engine_applies_wal_autocheckpoint() -> None:
      engine = create_sqlite_engine(
          DatabaseConfig(path=new_database_path(), wal_autocheckpoint=321)
      )
      try:
          with engine.connect() as connection:
              actual = connection.execute(text("PRAGMA wal_autocheckpoint")).scalar_one()
      finally:
          engine.dispose()
      assert actual == 321
  ```

- [ ] **Step 6: Run the new database tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage/test_database.py -q --basetemp='.cache/pytest-database-red'`

  Expected: construction fails because `DatabaseConfig` has no `wal_autocheckpoint` parameter.

- [ ] **Step 7: Add and apply `wal_autocheckpoint`**

  Add `wal_autocheckpoint: int = 1_000`, reject negative values, and execute `PRAGMA wal_autocheckpoint=<value>` after the journal-mode pragma on every connection. Keep `foreign_keys=ON`, configured `busy_timeout`, and `synchronous` behavior unchanged.

- [ ] **Step 8: Run all storage unit tests**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/unit/storage -q --basetemp='.cache/pytest-storage'`

  Expected: all storage unit tests pass.

### Task 4: Enforce the Workspace boundary in ArtifactStore

**Files:**
- Modify: `src/ppt_agent/artifacts/store.py`
- Modify: `tests/integration/artifacts/test_artifact_store.py`

**Interfaces:**
- Consumes: `WorkspacePaths.resolve()` before filesystem access and `WorkspacePaths.verify()` after writes and renames.
- Produces: unchanged public `ArtifactStore` API, with `UnsafeWorkspacePath` translated to `ArtifactIntegrityError` at the Artifact boundary.

- [ ] **Step 1: Write failing Artifact escape/race regression tests**

  Add tests that mutate persisted `staging_path`/`relative_path` to parent traversal, absolute, mixed-separator traversal, and a symlink/junction escape and assert read/write/commit/reconcile fail with `ArtifactIntegrityError` without creating or moving a file outside the Workspace. Add a post-write race test via a narrow test hook or monkeypatch of `WorkspacePaths.verify` that swaps an internal directory for an outside link immediately before final verification; prove the write is rejected. Keep real files and SQLite records; do not mock ArtifactStore behavior.

  ```python
  @pytest.mark.parametrize("unsafe", ("../outside.bin", r"..\outside.bin"))
  def test_artifact_store_rejects_persisted_escaping_path(
      artifact_store: StoreFixture, unsafe: str
  ) -> None:
      artifact_id = create_empty_artifact(artifact_store)
      set_staging_path(artifact_store.sessions, artifact_id, unsafe)
      with pytest.raises(ArtifactIntegrityError, match="workspace"):
          artifact_store.store.write_staging(artifact_id, [b"payload"])
  ```

- [ ] **Step 2: Run targeted Artifact tests and verify RED**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/artifacts/test_artifact_store.py -q --basetemp='.cache/pytest-artifact-security-red'`

  Expected: at least the mixed Windows separator and post-write verification tests fail against the current `_resolve()` implementation.

- [ ] **Step 3: Replace ArtifactStore path logic with `WorkspacePaths`**

  Construct `WorkspacePaths(workspace, create=True)` before creating internal directories. Route every stored staging, final, orphan, trash, and scan-derived relative path through it. Verify the opened path immediately before access and verify the resulting file after closing or `os.replace`; on failure, raise `ArtifactIntegrityError` while preserving enough staging/quarantine state for startup reconciliation. Keep content addressing, transaction boundaries, and existing in-process resource locks unchanged.

  ```python
  def _resolve(self, relative_path: str, *, must_exist: bool = False) -> Path:
      try:
          return self._paths.resolve(relative_path, must_exist=must_exist)
      except UnsafeWorkspacePath as exc:
          raise ArtifactIntegrityError(str(exc)) from exc
  ```

- [ ] **Step 4: Run Artifact integration tests and verify GREEN**

  Run: `& '.\.venv\Scripts\pytest.exe' tests/integration/artifacts/test_artifact_store.py -q --basetemp='.cache/pytest-artifact-security-green'`

  Expected: all existing lifecycle tests and new path-security regressions pass.

- [ ] **Step 5: Run the module and full verification suites**

  Run:

  ```powershell
  & '.\.venv\Scripts\pytest.exe' tests/unit/storage tests/integration/artifacts -q --basetemp='.cache/pytest-workspace-module'
  & '.\.venv\Scripts\pytest.exe' -q --basetemp='.cache/pytest-final'
  & '.\.venv\Scripts\python.exe' -m compileall -q src tests migrations
  git diff --check
  git status --short
  ```

  Expected: every command exits zero; the status lists only the planned source, test, and plan files.

- [ ] **Step 6: Request independent code review and resolve findings**

  Give the reviewer `docs/specs/07-local-security-and-storage.md`, this plan, the pre-module SHA, and the working-tree diff. Require review of Windows path semantics, link races, lock lifetime, cleanup safety, WAL fallback, and regression compatibility. Fix every Critical and Important finding with a new failing regression test before implementation, then repeat the full verification suite.

- [ ] **Step 7: Create the single functional commit**

  ```powershell
  git add docs/superpowers/plans/2026-09-22-workspace-storage-foundation.md src/ppt_agent/storage src/ppt_agent/artifacts/store.py tests/unit/storage tests/integration/artifacts/test_artifact_store.py
  git commit -m "feat: secure workspace storage foundation"
  ```

  Expected: one commit contains the complete Workspace/path-security module and no HTTP-authentication, parser-limit, backup, TaskSupervisor, or unrelated changes.

## Deferred Spec Coverage

- Local access token, short-lived session exchange, Host/Origin/CSRF, and API/SSE authentication: next “local HTTP authentication boundary” module.
- Upload/image/PDF/Markdown/decompression/OCR limits and restricted parser subprocesses: later “input ingestion safety” module.
- Untrusted source-content labels and tool-capability isolation: implemented with source ingestion and Agent tool control.
- Provider disclosure/Trace field classification: implemented with Provider adapters and settings UI.
- Online backup manifests, retention, restore integrity checks, and Artifact reconciliation: dedicated backup/restore module after application composition exists.
