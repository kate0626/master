#include "functions.hpp"

void test1(string filepath);
void test2(string filepath);
void test3(string filepath);
void test4(string filepath);

int main(int argc, char* argv[])
{
    test4(argv[1]);
    return 0;
}

void test4(string filepath)
{
    Graph graph = read_graph(filepath);
    CommunityGraph cg(graph);
    double prev = -1;
    double mod = cg.compute_modurality();
    auto start = chrono::steady_clock::now();
    while (mod - prev > 0) {
        prev = mod;
        cg.louvain();
        mod = cg.compute_modurality();
        cout << mod << endl;
        cout << endl;
    }
    auto end = chrono::steady_clock::now();
    auto duration = chrono::duration_cast<chrono::microseconds>(end - start);
    cout << (double)duration.count() / 1000000 << " s" << endl;
}

void test3(string filepath)
{
    Graph graph = read_graph(filepath);
    CommunityGraph cg(graph);
    cout << cg.compute_modurality() << endl;
    cout << "2 -> 1: " << cg.compute_update(2, 1) << endl;
    cg.move_community_into_another(2, 1);
    cout << cg.compute_modurality() << endl;
    cout << "3 -> 1: " << cg.compute_update(3, 1) << endl;
    cg.move_community_into_another(3, 1);
    cout << cg.compute_modurality() << endl;
    cout << "5 -> 4: " << cg.compute_update(5, 4) << endl;
    cg.move_community_into_another(5, 4);
    cout << cg.compute_modurality() << endl;
    cout << "2 -> 4: " << cg.compute_update(6, 4) << endl;
    cg.move_community_into_another(6, 4);
    cout << cg.compute_modurality() << endl;
    cout << "8 -> 7: " << cg.compute_update(8, 7) << endl;
    cg.move_community_into_another(8, 7);
    cout << cg.compute_modurality() << endl;
    cout << "9 -> 7: " << cg.compute_update(9, 7) << endl;
    cg.move_community_into_another(9, 7);
    cout << cg.compute_modurality() << endl;
    cout << "10 -> 7: " << cg.compute_update(10, 7) << endl;
    cg.move_community_into_another(10, 7);
    cout << cg.compute_modurality() << endl;
    cout << "modularity = " << cg.compute_modurality() << endl;
    cg.print_communities();
}

void test2(string filepath)
{
    Graph graph = read_graph(filepath);
    double best_val = 0;
    unordered_map<int, unordered_set<int>> nodes_in_communities;
    for (int i = 0; i < 1000; ++i) {
        CommunityGraph cg(graph);
        cg.louvain();
        double mod = cg.compute_modurality();
        if (mod > best_val) {
            best_val = mod;
            nodes_in_communities = cg.get_ndoes_in_communities();
            cout << "best modularity updated: " << best_val << endl;
        }
    }
    cout << best_val << endl;
    print_nodes_in_community(nodes_in_communities);
}

void test1(string filepath)
{
    Graph graph = read_graph(filepath);
    CommunityGraph cg(graph);
    cg.move_community_into_another(1, 0);
    cg.move_community_into_another(2, 0);
    cg.move_community_into_another(3, 0);
    cg.move_community_into_another(5, 4);
    cg.move_community_into_another(6, 4);
    cg.move_community_into_another(8, 7);
    cg.move_community_into_another(9, 7);
    cg.louvain();
    cout << "modularity = " << cg.compute_modurality() << endl;
}
