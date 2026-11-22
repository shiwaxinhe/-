"""Create fresh, local lab identities. Requires OpenSSL 3 on PATH or Git for Windows."""
import argparse
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

def provision(directory,server_ip=None):
    directory = Path(directory)
    directory.parent.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("openssl") or str(Path(os.environ.get("ProgramFiles", "")) / "Git/usr/bin/openssl.exe")
    if directory.exists():
        raise FileExistsError(f"Refusing to replace {directory}")
    with tempfile.TemporaryDirectory(dir=directory.parent) as temp:
        out = Path(temp) / "identities"
        out.mkdir()
        def run(*args):
            subprocess.run([exe, *args], cwd=out, check=True, capture_output=True)
        run("req","-x509","-newkey","rsa:3072","-nodes","-days","30","-sha256","-subj","/CN=EX1B Lab CA","-addext","basicConstraints=critical,CA:TRUE","-addext","keyUsage=critical,keyCertSign,cRLSign","-keyout","ca.key","-out","ca.pem")
        for identity in ("server","alice","bob"):
            run("req","-new","-newkey","rsa:2048","-nodes","-subj","/CN="+identity,"-keyout",identity+".key","-out","pending.csr")
            is_server = identity == "server"
            usage = "serverAuth" if is_server else "clientAuth"
            ext = f"basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage={usage}\n"
            if is_server:
                ext += "subjectAltName=DNS:localhost,IP:127.0.0.1\n"
                if server_ip:
                    ext=ext.rstrip()+",IP:"+str(ipaddress.ip_address(server_ip))+"\n"
            (out/"ext.cnf").write_text(ext, encoding="ascii")
            run("x509","-req","-in","pending.csr","-CA","ca.pem","-CAkey","ca.key","-CAcreateserial","-days","14","-sha256","-extfile","ext.cnf","-out",identity+".pem")
        for name in ("ca.key","ca.srl","pending.csr","ext.cnf"):
            (out/name).unlink(missing_ok=True)
        out.rename(directory)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="state/keys")
    parser.add_argument("--server-ip")
    args=parser.parse_args()
    provision(args.out,args.server_ip)
    print("Created CA certificate and separate server, alice and bob identities.")

