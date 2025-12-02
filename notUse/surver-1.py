import random
import hashlib
import rsa


class Community:
    def __init__(self, name):
        self.name = name
        self.pubkey, self.privkey = rsa.newkeys(512)

    def sign(self, message: str) -> bytes:
        return rsa.sign(message.encode(), self.privkey, "SHA-256")

    def verify(self, message: str, signature: bytes, pubkey) -> bool:
        try:
            rsa.verify(message.encode(), signature, pubkey)
            return True
        except:
            return False


class Node:
    def __init__(self, node_id, community):
        self.id = node_id
        self.community = community
        self.neighbors = []

    def add_neighbor(self, node):
        self.neighbors.append(node)


class RandomWalker:
    def __init__(self, start_node, token="valid_token", max_hops=10):
        self.current_node = start_node
        self.token = token
        self.remaining_hops = max_hops

    def walk(self):
        path = [self.current_node.id]
        while self.remaining_hops > 0:
            if not self.current_node.neighbors:
                break
            next_node = random.choice(self.current_node.neighbors)

            # コミュニティが変わる場合に認証
            if next_node.community != self.current_node.community:
                message = f"{self.current_node.id}->{next_node.id}:{self.token}"
                signature = self.current_node.community.sign(message)
                verified = next_node.community.verify(
                    message, signature, self.current_node.community.pubkey
                )
                if not verified or self.token != "valid_token":
                    print(f"認証失敗: {self.current_node.id} -> {next_node.id}")
                    break
                else:
                    print(
                        f"認証成功: {self.current_node.community.name} -> {next_node.community.name}"
                    )

            # hop 進める
            self.current_node = next_node
            path.append(self.current_node.id)
            self.remaining_hops -= 1
        return path


# --- 簡単な例 ---
c1 = Community("ServerA")
c2 = Community("ServerB")

n1 = Node(1, c1)
n2 = Node(2, c1)
n3 = Node(3, c2)
n4 = Node(4, c2)

n1.add_neighbor(n2)
n2.add_neighbor(n3)  # サーバ境界を跨ぐ
n3.add_neighbor(n4)

walker = RandomWalker(n1, token="valid_token", max_hops=5)
print("RW Path:", walker.walk())
