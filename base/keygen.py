# まず以下のコマンドを実行する
# pip install flask pynacl requests
# keygen.py

from nacl.signing import SigningKey
import json


def gen_and_save(path_priv, path_pub):

    sk = SigningKey.generate()
    pk = sk.verify_key
    with open(path_priv, "wb") as f:
        f.write(sk.encode())
    with open(path_pub, "wb") as f:
        f.write(pk.encode())


if __name__ == "__main__":
    gen_and_save("serverA_priv.key", "serverA_pub.key")
    gen_and_save("serverB_priv.key", "serverB_pub.key")
