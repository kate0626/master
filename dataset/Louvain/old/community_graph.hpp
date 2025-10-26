#include "graph.hpp"
#define MIN 0.0000001

class CommunityGraph {
private:
    // typedef
    typedef pair<int, int> edge;

    // community
    unordered_map<int, unordered_set<int>> nodes_in_communities;
    unordered_map<int, unordered_map<int, int>> neighbors;
    unordered_map<int, int> inside_weights;
    unordered_map<int, int> attached_weights;

    // vertices
    unordered_map<int, int> community_of_vertices;

    int sum_weights;

    // debug
    chrono::microseconds duration;

public:
    CommunityGraph(Graph graph)
    {
        sum_weights = 0;
        vector<int> vertices = graph.get_vertices();
        for (auto v : vertices) {
            add_community(v);
            add_vertex_to_community(v, v);
        }
        for (auto v : vertices) {
            for (auto nbr : graph.get_neighbors(v)) {
                if (v < nbr)
                    add_weight_to_edge(v, nbr, 1);
            }
        }

        sum_weights = graph.get_num_edges();

        cout << "nodes: " << neighbors.size() << ", edges: " << sum_weights << endl;
        duration = (chrono::microseconds)0;
    }

    // ---------------------------------------------------
    // getters
    // ---------------------------------------------------
    bool
    has_community(int c)
    {
        return nodes_in_communities.find(c) != nodes_in_communities.end();
    }

    bool has_vertex(int v)
    {
        return community_of_vertices.find(v) != community_of_vertices.end();
    }

    bool has_edge(int c1, int c2)
    {
        if (!has_community(c1) or !has_community(c2))
            return false;
        return neighbors[c1].find(c2) != neighbors[c1].end();
    }

    bool has_vertex_in_community(int c, int v)
    {
        if (!has_vertex(v)) {
            cout << "vertex " << c << " not exists" << endl;
            return false;
        }

        return community_of_vertices[v] == c;
    }

    int get_edge_weight(int c1, int c2)
    {
        if (!has_edge(c1, c2))
            return -1;
        return neighbors[c1][c2];
    }

    vector<int> get_neighbors(int c)
    {
        vector<int> nbrs;
        for (auto [nbr, weight] : neighbors[c])
            nbrs.push_back(nbr);
        return nbrs;
    }

    vector<int> get_communities()
    {
        vector<int> communities;
        for (auto [c, nodes] : nodes_in_communities) {
            communities.push_back(c);
        }
        return communities;
    }

    unordered_map<int, unordered_set<int>> get_ndoes_in_communities()
    {
        return nodes_in_communities;
    }

    // ---------------------------------------------------
    // setters
    // ---------------------------------------------------

    void add_community(int c)
    {
        if (has_community(c)) {
            cout << "community " << c << " already exists" << endl;
            return;
        }

        nodes_in_communities[c] = unordered_set<int>();
        neighbors[c] = unordered_map<int, int>();
        inside_weights[c] = 0;
        attached_weights[c] = 0;
    }

    void add_vertex_to_community(int c, int v)
    {
        if (!has_community(c)) {
            cout << "community not exists" << endl;
            return;
        }

        nodes_in_communities[c].insert(v);
        community_of_vertices[v] = c;
    }

    void remove_vertex_from_community(int c, int v)
    {
        if (!has_community(c)) {
            cout << "community not exists" << endl;
            return;
        }
        if (!has_vertex(v)) {
            cout << "vertex not exists" << endl;
            return;
        }
        if (!has_vertex(v)) {
            cout << "vertex not exists" << endl;
            return;
        }

        nodes_in_communities[c].erase(v);
        community_of_vertices.erase(v);
    }

    void add_weight_to_edge(int c1, int c2, int weight)
    {
        if (!has_community(c1) || !has_community(c2)) {
            cout << "community not exists yeah" << endl;
            return;
        }

        if (!has_edge(c1, c2)) {
            neighbors[c1][c2] = 0;
            neighbors[c2][c1] = 0;
        }

        neighbors[c1][c2] += weight;
        neighbors[c2][c1] += weight;
        attached_weights[c1] += weight;
        attached_weights[c2] += weight;

        if (c1 == c2) {
            neighbors[c1][c2] -= weight;
            attached_weights[c1] -= weight;
            inside_weights[c1] += weight;
        }
    }

    void remove_edge(int from, int to)
    {
        if (!has_community(from) || !has_community(to)) {
            cout << "community not exists" << endl;
            return;
        }

        if (!has_edge(from, to)) {
            cout << "edge not exists" << endl;
            return;
        }
        attached_weights[to] -= neighbors[from][to];
        neighbors[to].erase(from);
    }

    void remove_community(int c)
    {
        if (!has_community(c)) {
            cout << "community not exists" << endl;
            return;
        }
        for (auto neighbor : get_neighbors(c))
            remove_edge(c, neighbor);

        auto start = chrono::steady_clock::now();
        neighbors.erase(c);
        nodes_in_communities.erase(c);
        inside_weights.erase(c);
        attached_weights.erase(c);
        auto end = chrono::steady_clock::now();
        duration += chrono::duration_cast<chrono::microseconds>(end - start);
    }

    void move_community_into_another(int s, int t)
    {
        if (!has_community(s) || !has_community(t)) {
            cout << "community not exists" << endl;
            return;
        }

        if (s == t) {
            cout << "communities must not be same" << endl;
            return;
        }

        // merge s to t
        for (auto v : nodes_in_communities[s]) {
            nodes_in_communities[t].insert(v);
            community_of_vertices[v] = t;
        }
        for (auto [nbr, weight] : neighbors[s]) {
            if (nbr == s)
                continue;
            if (nbr == t) {
                add_weight_to_edge(t, t, weight);
            }
            add_weight_to_edge(t, nbr, weight);
        }
        add_weight_to_edge(t, t, neighbors[s][s]);

        remove_community(s);
    }

    // ---------------------------------------------------
    // computation
    // ---------------------------------------------------

    double compute_modurality()
    {
        double modularity = 0;
        for (auto [c, inside_weight] : inside_weights) {
            modularity += (double)inside_weight / (2 * sum_weights);
            double attached_weight = attached_weights[c];
            modularity -= attached_weight / (2 * sum_weights) * attached_weight / (2 * sum_weights);
        }
        return modularity;
    }

    // ---------------------------------------------------
    // Louvain
    // ---------------------------------------------------

    double compute_update(int from, int to)
    {
        double sigma_in = inside_weights[to];
        double sigma_tot = attached_weights[to];
        double ki = attached_weights[from];
        double ki_in = neighbors[from][to];
        double m = sum_weights;
        // double delta_q = (sigma_in + 2 * ki_in) / (2 * m) - (sigma_tot + ki) * (sigma_tot + ki) / (4 * m * m);
        // delta_q -= sigma_in / (2 * m) - sigma_tot * sigma_tot / (4 * m * m) - ki * ki / (4 * m * m);
        return ki_in / m - sigma_tot / (2 * m) * ki / m;
    }

    double louvain()
    {
        auto start = chrono::steady_clock::now();
        auto end = chrono::steady_clock::now();
        auto move = chrono::duration_cast<chrono::microseconds>(end - end);
        double prev = compute_modurality();
        // for all community
        while (true) {
            // randomize the order of vertices
            vector<int> communities = get_communities();
            random_device rd;
            mt19937 g(rd());
            shuffle(communities.begin(), communities.end(), g);
            cout << communities.size() << endl;

            for (auto from : communities) {
                int best_community = from;
                double best_increase = 0;
                for (auto [to, weight] : neighbors[from]) {
                    if (from == to)
                        continue;
                    if (!has_community(to))
                        continue;
                    double delta_q = compute_update(from, to);
                    if (delta_q > best_increase) {
                        best_increase = delta_q;
                        best_community = to;
                    }
                }
                if (best_increase <= 0)
                    continue;
                // cout << best_increase << endl;
                start = chrono::steady_clock::now();
                move_community_into_another(from, best_community);
                end = chrono::steady_clock::now();
                move += chrono::duration_cast<chrono::microseconds>(end - start);
            }
            double res = compute_modurality();
            cout << prev << " ---> " << res << endl;
            if (res - prev <= MIN)
                break;
            prev = res;
        }
        // cout << "move: " << (double)move.count() / 1000000 << "s" << endl;
        // cout << "duration: " << (double)duration.count() / 1000000 << "s" << endl;
        return prev;
    }

    // ---------------------------------------------------
    // debug
    // ---------------------------------------------------

    void print_communities()
    {
        cout << "communities" << endl;
        for (auto [community, vertices] : nodes_in_communities) {
            cout << community << ":";
            for (auto vertex : vertices) {
                cout << " " << vertex;
            }
            cout << endl;
        }
    }

    void print_weights()
    {
        cout << "weight" << endl;
        for (auto [community, weight] : inside_weights) {
            cout << community << "; inside: " << weight << ", attached: " << attached_weights[community] << endl;
        }
    }

    void print_edges()
    {
        cout << "edges" << endl;
        for (auto [c1, nbrs] : neighbors)
            for (auto [c2, weight] : nbrs)
                cout << "(" << c1 << ", " << c2 << "): " << weight << endl;
    }

    void print_neighbors()
    {
        cout << "neighbors" << endl;
        for (auto [v, nbrs] : neighbors) {
            cout << v << ":";
            for (auto [nbr, weight] : nbrs) {
                cout << " " << nbr;
            }
            cout << endl;
        }
    }

    void print_vertices()
    {
        cout << "vertices" << endl;
        for (auto [v, c] : community_of_vertices)
            cout << v << ": " << c << endl;
    }
};