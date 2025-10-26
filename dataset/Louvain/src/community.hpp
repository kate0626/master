#include "graph.cpp"

class Community {
public:
    Graph g;

    // number of nodes in the network and size of all vectors
    int size;

    // community to which each node belongs
    vector<int> community_of;

    // used to compute the modularity participation of each community
    vector<int> in, tot;

    // a new pass is computed if the last one has generated an increase
    // greater than min_modularity
    // if 0. even a minor increase is enough to go for one more pass
    double min_modularity;

    // resolution
    double resolution;

    // constructors
    // reads graph from file using graph constructor
    Community(string filename, int type, double min_modularity, double rsl = 1);
    // copy graph
    Community(Graph g, double min_modularity, double rsl = 1);

    // display the community of each node
    void display();

    // remove the node from its current communtiy with which it has dnodecomm links
    inline void remove(int node, int comm, int dnodecomm);

    // insert the node in comm with which it shares dnodecomm links
    inline void insert(int node, int comm, int dnodecomm);

    // compute the gain of modularity if node where inserted in comm
    // given that node has dnodecomm links to comm.  The formula is:
    // [(In(comm)+2d(node,comm))/2m - ((tot(comm)+deg(node))/2m)^2]-
    // [In(comm)/2m - (tot(comm)/2m)^2 - (deg(node)/2m)^2]
    // where In(comm)    = number of half-links strictly inside comm
    //       Tot(comm)   = number of half-links inside or outside comm (sum(degrees))
    //       d(node,com) = number of links from node to comm
    //       deg(node)   = node degree
    //       m           = number of links
    inline double modularity_gain(int node, int comm, int dnodecomm);

    // compute the set of neighboring communities of node
    // for each community, gives the number of links from node to comm
    map<int, int> neighboring_communities(int node);

    // compute the modularity of the curernt partition
    double modularity();

    // displays the graph of communities as computed by one_level
    void partition2graph();

    // displays the current partition (with communities renumberd from 0 to k-1)
    void display_partition();

    // generates the graph of communities as computed by one_level
    Graph partition2graph_binary();

    // compute communities of the graph for one level
    // return the modularity
    double one_level();

    vector<int> generate_random_order(int size);
};

inline void Community::remove(int node, int comm, int dnodecomm)
{
    assert(node >= 0 && node < size);
    tot[comm] -= g.weighted_degree(node);
    in[comm] -= 2 * dnodecomm + g.num_selfloops(node);
    community_of[node] = -1;
}

inline void Community::insert(int node, int comm, int dnodecomm)
{
    assert(node >= 0 && node < size);

    tot[comm] += g.weighted_degree(node);
    in[comm] += 2 * dnodecomm + g.num_selfloops(node);
    community_of[node] = comm;
}

inline double Community::modularity_gain(int node, int comm, int dnodecomm)
{
    assert(node >= 0 && node < size);

    double totc = (double)tot[comm];
    double degc = (double)g.weighted_degree(node);
    double m2 = (double)g.total_weight;
    double dnc = (double)dnodecomm;

    return (dnc - resolution * totc * degc / m2);
}