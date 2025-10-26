#include "header.hpp"

class Graph {
private:
    typedef pair<int, int> edge;
    unordered_map<int, unordered_set<int>> neighbors;
    int num_edges;

    void add_vertex(int v)
    {
        if (has_vertex(v))
            return;
        neighbors[v] = unordered_set<int>();
    }

public:
    Graph()
    {
        num_edges = 0;
    }

    // getter
    bool has_vertex(int v)
    {
        return neighbors.find(v) != neighbors.end();
    }

    // We assume v is a valid node
    // Otherwise an exception will be triggered
    int get_degree(int v)
    {
        return neighbors[v].size();
    }

    int get_num_edges()
    {
        return num_edges;
    }

    // We assume v is a valid node
    // Otherwise an exception will be triggered
    unordered_set<int> get_neighbors(int v)
    {
        return neighbors[v];
    }

    vector<int> get_vertices()
    {
        vector<int> vertices;
        for (auto [v, nbrs] : neighbors) {
            vertices.push_back(v);
        }
        return vertices;
    }

    void print_graph()
    {
        for (auto [u, nbrs] : neighbors) {
            cout << u << ":";
            for (auto v : nbrs) {
                cout << " " << v;
            }
            cout << endl;
        }
    }

    bool has_neighbor(int u, int v)
    {
        return neighbors[u].find(v) != neighbors[u].end();
    }

    double compute_modularity(vector<vector<int>> communities)
    {
        double modularity = 0;
        for (auto community : communities) {
            // create community set
            unordered_set<int> nodes_in_community;
            for (auto node : community)
                nodes_in_community.insert(node);

            int num_edges_within_community = 0;
            int num_edges_attached_to_community = 0;

            for (auto node : community) {
                for (auto neighbor : get_neighbors(node))
                    if (nodes_in_community.find(neighbor) != nodes_in_community.end())
                        ++num_edges_within_community;
                num_edges_attached_to_community += get_degree(node);
            }
            modularity += (double)num_edges_within_community / (2 * num_edges);
            modularity -= (double)num_edges_attached_to_community * num_edges_attached_to_community / (4 * num_edges * num_edges);
        }
        return modularity;
    }

    // setter
    void add_edge(int u, int v)
    {
        add_vertex(u);
        add_vertex(v);
        if (!has_neighbor(u, v) and !has_neighbor(v, u))
            ++num_edges;
        neighbors[u].insert(v);
        neighbors[v].insert(u);
    }
};