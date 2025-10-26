#include "graph.hpp"

Graph::Graph()
{
    num_nodes = 0;
    num_links = 0;
    total_weight = 0;
}

Graph::Graph(string filepath, int type)
{
    num_nodes = 0;
    num_links = 0;
    vector<vector<pair<int, float>>> all_links = read_file(filepath);
    renumber(all_links);
    num_nodes = all_links.size();

    // cumulative degree sequence
    unsigned long cumulative = 0;
    for (int i = 0; i < all_links.size(); ++i) {
        cumulative += all_links[i].size();
        degrees.push_back(cumulative);
    }

    // links
    for (int i = 0; i < all_links.size(); ++i)
        for (int j = 0; j < all_links[i].size(); ++j)
            links.push_back(all_links[i][j].first);

    // weights
    total_weight = 2 * num_links;
}

vector<vector<pair<int, float>>> Graph::read_file(string filepath)
{
    ifstream finput(filepath);
    assert(finput.good());

    // read from file
    vector<vector<pair<int, float>>> all_links;
    int u, v;
    while (finput >> u >> v) {
        if (all_links.size() <= max(u, v) + 1)
            all_links.resize(max(u, v) + 1);

        all_links[u].push_back(make_pair(v, 1));
        if (u != v)
            all_links[v].push_back(make_pair(u, 1));
        ++num_links;
    }
    finput.close();

    return all_links;
}

void Graph::renumber(vector<vector<pair<int, float>>>& all_links)
{
    vector<bool> linked(all_links.size(), false);
    vector<int> renum(all_links.size(), -1);
    int nb = 0;

    for (int i = 0; i < all_links.size(); ++i) {
        for (int j = 0; j < all_links[i].size(); j++) {
            linked[i] = true;
            linked[all_links[i][j].first] = true;
        }
    }

    for (int i = 0; i < all_links.size(); ++i) {
        if (linked[i]) {
            renum[i] = nb;
            original_id_to_node_id[i] = nb;
            ++nb;
        }
    }

    for (int i = 0; i < all_links.size(); ++i) {
        if (!linked[i])
            continue;
        for (int j = 0; j < all_links[i].size(); ++j) {
            all_links[i][j].first = renum[all_links[i][j].first];
        }
        all_links[renum[i]] = all_links[i];
    }

    all_links.resize(nb);
}

void Graph::display()
{
    for (int node = 0; node < num_nodes; node++) {
        pair<int, int> indices = neighbors(node);
        int link_index = indices.first;
        int weight_index = indices.second;
        for (int i = 0; i < num_neighbors(node); i++) {
            if (weights.size() == 0)
                cout << node << " " << links[link_index + i] << " " << weights[weight_index + i] << endl;
            else {
                cout << (node + 1) << " " << (links[link_index + i] + 1) << endl;
            }
        }
    }
}