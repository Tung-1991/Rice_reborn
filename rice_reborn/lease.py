"""OS-backed single-runtime lock, released automatically if the process dies."""
import os

class RuntimeLease:
    def __init__(self,path):
        self.file=open(path,'a+b')
        self.file.seek(0,os.SEEK_END)
        if self.file.tell()==0: self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close(); raise RuntimeError('Đã có backend dùng runtime này; dừng bản đang chạy trước.') from None
    def close(self):
        if self.file.closed: return
        if os.name=='nt':
            import msvcrt
            self.file.seek(0); msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
        self.file.close()
