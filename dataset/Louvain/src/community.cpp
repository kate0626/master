#include "community.hpp"

Community::Community(string filename, int type, double minm, double rsl)
{
    g = Graph(filename, type);
    size = g.num_nodes;
    community_of.resize(size);
    in.resize(size);
    tot.resize(size);

    for (int i = 0; i < size; ++i) {
        community_of[i] = i;
        in[i] = g.num_selfloops(i);
        tot[i] = g.weighted_degree(i);
    }
    min_modularity = minm;
    resolution = rsl;
}

Community::Community(Graph gc, double minm, double rsl)
{
    g = gc;
    size = g.num_nodes;

    community_of.resize(size);
    in.resize(size);
    tot.resize(size);

    for (int i = 0; i < size; ++i) {
        community_of[i] = i;
        in[i] = g.num_selfloops(i);
        tot[i] = g.weighted_degree(i);
    }
    min_modularity = minm;
    resolution = rsl;
}

void Community::display()
{
    cerr << endl
         << "<";
    for (int i = 0; i < size; i++)
        cerr << " " << i << "/" << community_of[i] << "/" << in[i] << "/" << tot[i];
    cerr << ">" << endl;
}

double Community::modularity()
{
    double q = 0.;
    double m2 = (double)g.total_weight;

    for (int i = 0; i < size; ++i) {
        if (tot[i] > 0)
            q += (double)in[i] / m2 - resolution * (double(tot[i] / m2) * ((double)tot[i] / m2));
    }

    return q;
}

map<int, int> Community::neighboring_communities(int node)
{
    map<int, int> res;
    pair<int, int> indices = g.neighbors(node);
    int link_index = indices.first;
    int weight_index = indices.second;

    int deg = g.num_neighbors(node);

    res.insert(make_pair(community_of[node], 0));

    for (int i = 0; i < deg; ++i) {
        int neigh = g.links[indices.first + i];
        int neighboring_communities = community_of[neigh];
        int neigh_weight = (g.weights.size() == 0) ? 1 : g.weights[weight_index + i];

        if (neigh == node)
            continue;
        map<int, int>::iterator it = res.find(neighboring_communities);
        if (it != res.end())
            it->second += neigh_weight;
        else
            res.insert(make_pair(neighboring_communities, neigh_weight));
    }

    return res;
}

void Community::partition2graph()
{
    vector<int> renumber(size, -1);
    for (int node = 0; node < size; ++node)
        ++renumber[community_of[node]];

    int final = 0;
    for (int i = 0; i < size; ++i)
        if (renumber[i] > 0)
            renumber[i] = final++;

    for (int i = 0; i < size; ++i) {
        pair<int, int> indices = g.neighbors(i);
        int deg = g.num_neighbors(i);
        for (int j = 0; j < deg; ++j) {
            int neigh = g.links[indices.first + j];
            cout << renumber[community_of[i]] << " " << renumber[community_of[neigh]] << endl;
        }
    }
}

void Community::display_partition()
{
    vector<int> renumber(size, -1);

    int final = 0;
    for (int i = 0; i < size; ++i)
        if (renumber[i] != -1)
            renumber[i] = final++;
    for (int i = 0; i < size; ++i)
        cout << i << " " << renumber[community_of[i]] << endl;
}

Graph Community::partition2graph_binary()
{
    vector<int> renumber(size, -1);
    for (int node = 0; node < size; ++node)
        ++renumber[community_of[node]];

    int final = 0;
    for (int i = 0; i < size; ++i)
        if (renumber[i] >= 0)
            renumber[i] = final++;

    vector<vector<int>> comm_nodes(final);
    for (int node = 0; node < size; ++node)
        comm_nodes[renumber[community_of[node]]].push_back(node);

    // unweighted to weighted
    Graph g2;
    g2.num_nodes = comm_nodes.size();
    g2.degrees.resize(comm_nodes.size(), -1);
    g2.links.resize(g.links.size(), -1);
    g2.weights.resize(g.links.size(), -1);

    for (int i = 0; i < size; ++i)
        if (renumber[i] >= 0)
            g2.original_id_to_node_id[i] = renumber[i];

    long where = 0;
    int comm_deg = comm_nodes.size();
    for (int comm = 0; comm < comm_deg; ++comm) {
        map<int, int> m;
        map<int, int>::iterator it;

        int comm_size = comm_nodes[comm].size();
        for (int node = 0; node < comm_size; ++node) {
            pair<int, int> indices = g.neighbors(comm_nodes[comm][node]);
            int link_index = indices.first;
            int weight_index = indices.second;
            int deg = g.num_neighbors(comm_nodes[comm][node]);
            for (int i = 0; i < deg; ++i) {
                int neigh = g.links[indices.first + i];
                int neighboring_communities = renumber[community_of[neigh]];
                int neigh_weight = (g.weights.size() == 0) ? 1 : g.weights[weight_index + i];

                it = m.find(neighboring_communities);
                if (it == m.end())
                    m.insert(make_pair(neighboring_communities, neigh_weight));
                else
                    it->second += neigh_weight;
            }
        }

        g2.degrees[comm] = (comm == 0) ? m.size() : g2.degrees[comm - 1] + m.size();
        g2.num_links += m.size();

        // for (it = m.begin(); it != m.end(); ++it) {
        //     g2.total_weight += it->second;
        //     g2.links[where] = it->first;
        //     g2.weights[where] = it->second;
        //     ++where;
        // }
        for (auto item : m) {
            g2.total_weight += item.second;
            g2.links[where] = item.first;
            g2.weights[where] = item.second;
            ++where;
        }
    }

    // correct here?
    g.links.resize((long)g2.num_links);
    g.weights.resize((long)g2.num_links);

    return g2;
}

vector<int> Community::generate_random_order(int size)
{
    vector<int> random_order(size);
    for (int i = 0; i < size; i++)
        random_order[i] = i;
    for (int i = 0; i < size - 1; i++) {
        int rand_pos = rand() % (size - i) + i;
        int tmp = random_order[i];
        random_order[i] = random_order[rand_pos];
        random_order[rand_pos] = tmp;
    }
    return random_order;
}

double Community::one_level()
{
    int num_pass_done = 0;
    double new_mod = modularity();
    double cur_mod = -1;
    vector<int> random_order = generate_random_order(size);

    // repeat while
    //   there is an improvement of modularity
    //   or there is an improvement of modularity greater than a given epsilon
    //   or a predefined number of pass have been done
    while (new_mod - cur_mod > min_modularity) {
        cur_mod = new_mod;
        num_pass_done++;
        // for each node: remove the node from its community and insert it in the best community
        for (int node = 0; node < size; node++) {
            int community = community_of[node];

            // computation of all neighboring communities of current node
            map<int, int> nbr_communities = neighboring_communities(node);

            // remove node from its current community
            remove(node, community, nbr_communities.find(community)->second);

            // compute the nearest community for node
            // default choice for future insertion is the former community
            int best_community = community;
            int best_num_links = 0; // nbr_communities.find(community)->second;
            double best_increase = 0.; // modularity_gain(node, best_community, best_num_links);
            for (auto c : nbr_communities) {
                double increase = modularity_gain(node, c.first, c.second);
                if (increase > best_increase) {
                    best_community = c.first;
                    best_num_links = c.second;
                    best_increase = increase;
                }
            }

            // insert node in the nearest community
            //      cerr << "insert " << node << " in " << best_community << " " << best_increase << endl;
            insert(node, best_community, best_num_links);
        }

        new_mod = modularity();
        cerr << "pass number " << num_pass_done << ": " << cur_mod << " ---> " << new_mod << endl;
    }

    return new_mod;
}