#include "community.cpp"

#define PRECISION 0.000001
#define DISPLAY_LEVEL -2

void display_time(const char* str)
{
    time_t rawtime;
    time(&rawtime);
    cerr << str << " : " << ctime(&rawtime);
}

void update_original_node_community(unordered_map<int, int>& original_id_to_community, vector<int>& community_of, unordered_map<int, int>& renum)
{
    for (auto [node, c] : original_id_to_community) {
        int community = community_of[c];
        int new_community = renum[community];
        original_id_to_community[node] = new_community;
    }
}

int main(int argc, char** argv)
{
    srand(time(NULL));

    time_t time_begin, time_end;
    time(&time_begin);
    display_time("start");

    string filepath = argv[1];

    Community c(filepath, UNWEIGHTED, PRECISION);

    display_time("file read");
    // c.g.print_links();
    // c.g.print_degrees();

    double mod = c.modularity();

    cerr << "network : "
         << c.g.num_nodes << " nodes, "
         << c.g.num_links << " links, "
         << c.g.total_weight << " weight." << endl;

    double new_mod = c.one_level();
    unordered_map<int, int> original_id_to_community;
    for (auto [node, c] : c.g.original_id_to_node_id) {
        original_id_to_community[node] = c;
    }

    display_time("communities computed");
    cerr << "modularity increased from " << mod << " to " << new_mod << endl;

    if (DISPLAY_LEVEL == -1)
        c.display_partition();

    Graph g = c.partition2graph_binary();
    update_original_node_community(original_id_to_community, c.community_of, g.original_id_to_node_id);

    display_time("network of communities computed");

    int level = 0;
    while (new_mod - mod > PRECISION) {
        mod = new_mod;
        Community c(g, PRECISION);

        cerr << "\nnetwork : "
             << c.g.num_nodes << " nodes, "
             << c.g.num_links << " links, "
             << c.g.total_weight << " weight." << endl;

        new_mod = c.one_level();

        display_time("communities computed");
        cerr << "modularity increased from " << mod << " to " << new_mod << endl;

        if (DISPLAY_LEVEL == -1)
            c.display_partition();

        g = c.partition2graph_binary();
        level++;

        update_original_node_community(original_id_to_community, c.community_of, g.original_id_to_node_id);

        if (level == DISPLAY_LEVEL)
            g.display();

        display_time("network of communities computed");
    }
    time(&time_end);

    string output_path = "community/" + filepath.substr(6);
    output_path.replace(output_path.end() - 2, output_path.end(), "cm");
    cout << output_path << endl;
    ofstream output(output_path);

    for (auto [original, community] : original_id_to_community) {
        output << original << " " << community << endl;
    }

    cerr << PRECISION << " " << new_mod << " " << (time_end - time_begin) << endl;
}
