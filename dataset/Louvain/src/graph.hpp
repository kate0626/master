#include "header.hpp"

class Graph {
public:
    int num_nodes;
    unsigned long num_links;
    double total_weight;

    vector<unsigned long> degrees;
    vector<int> links;
    vector<float> weights;

    unordered_map<int, int> original_id_to_node_id;

    Graph();
    Graph(string filepath, int type);
    vector<vector<pair<int, float>>> read_file(string filepath);
    void renumber(vector<vector<pair<int, float>>>& all_links);

    void display();

    inline int num_neighbors(int node);
    inline int num_selfloops(int node);
    inline double weighted_degree(int node);

    // return pointers to the first neighbor and first weight of the node
    inline pair<int, int> neighbors(int node);

    void print_links()
    {
        cout << "[" << links[0];
        for (int i = 1; i < links.size(); ++i) {
            cout << ", " << links[i];
        }
        cout << "]" << endl;
    }

    void print_degrees()
    {
        cout << "[" << degrees[0];
        for (int i = 1; i < degrees.size(); ++i) {
            cout << ", " << degrees[i];
        }
        cout << "]" << endl;
    }
};

inline int Graph::num_neighbors(int node)
{
    assert(node >= 0 && node < num_nodes);

    if (node == 0)
        return degrees[0];
    else
        return degrees[node] - degrees[node - 1]; // cumulative sum?
}

// This method doesn't seem efficient
inline int Graph::num_selfloops(int node)
{
    assert(node >= 0 && node < num_nodes);
    pair<int, int> indices = neighbors(node);
    int link_index = indices.first;
    int weight_index = indices.second;

    for (int i = 0; i < num_neighbors(node); ++i) {
        if (links[link_index + i] != node)
            continue;
        if (weights.size() == 0)
            return 1;
        return weights[weight_index + i];
    }

    return 0;
}

inline double Graph::weighted_degree(int node)
{
    assert(node >= 0 && node < num_nodes);

    pair<int, int> indices = neighbors(node);
    int link_index = indices.first;
    int weight_index = indices.second;
    if (weights.size() == 0)
        return num_neighbors(node);
    int res = 0;
    for (int i = 0; i < num_neighbors(node); i++)
        res += weights[weight_index + i];
    return res;
}

inline pair<int, int> Graph::neighbors(int node)
{
    assert(node >= 0 && node < num_nodes);

    // if (node == 0)
    //     return make_pair(links.begin(), weights.begin());
    // if (weights.size() == 0)
    //     return make_pair(links.begin() + degrees[node - 1], weights.begin());
    // return make_pair(links.begin() + degrees[node - 1], weights.begin() + degrees[node - 1]);
    if (node == 0)
        return make_pair(0, 0);
    if (weights.size() == 0)
        return make_pair(degrees[node - 1], 0);
    return make_pair(degrees[node - 1], degrees[node - 1]);
}