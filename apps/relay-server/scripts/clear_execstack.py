"""네이티브 .so의 PT_GNU_STACK executable(PF_X) 플래그를 제거한다 (gVisor/Cloud Run 호환).

Cloud Run(gVisor)은 실행 가능 스택을 요구하는 공유 라이브러리 로드를 거부한다. onnxruntime 등
site-packages의 모든 확장 모듈에 일괄 적용한다(특정 패키지에 의존하지 않음 — 의존성이 바뀌어도 안전).

사용: python3 scripts/clear_execstack.py [venv_root]   (기본 /app/.venv)
"""
import struct
import sys
from pathlib import Path

_PT_GNU_STACK = 0x6474E551
_PF_X = 0x1


def clear_execstack(so: Path) -> bool:
    """so의 PT_GNU_STACK에서 PF_X를 제거. 변경했으면 True. ELF64가 아니면 no-op."""
    data = bytearray(so.read_bytes())
    if data[:4] != b"\x7fELF" or data[4] != 2:  # ELF64 LSB만 대상
        return False
    e_phoff = struct.unpack_from("<Q", data, 32)[0]
    e_phentsize = struct.unpack_from("<H", data, 54)[0]
    e_phnum = struct.unpack_from("<H", data, 56)[0]
    changed = False
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if struct.unpack_from("<I", data, off)[0] == _PT_GNU_STACK:
            flags = struct.unpack_from("<I", data, off + 4)[0]
            if flags & _PF_X:
                struct.pack_into("<I", data, off + 4, flags & ~_PF_X)
                changed = True
    if changed:
        so.write_bytes(data)
    return changed


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/.venv")
    cleared = []
    for so in root.rglob("*.so"):
        try:
            if clear_execstack(so):
                cleared.append(str(so))
        except Exception as e:  # 손상/비ELF 파일은 건너뜀
            print(f"skip {so}: {e}")
    print(f"Cleared execstack flag from {len(cleared)} .so file(s): {cleared}")


if __name__ == "__main__":
    main()
