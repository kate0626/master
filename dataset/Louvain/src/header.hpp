#include <algorithm>
#include <assert.h>
#include <chrono>
#include <climits>
#include <deque>
#include <fstream>
#include <iostream>
#include <map>
#include <queue>
#include <random>
#include <sstream>
#include <string>
#include <sys/mman.h>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#if defined(__MACH__)
#include <stdlib.h>
#else
#include <malloc.h>
#endif
#define WEIGHTED 0
#define UNWEIGHTED 1
using namespace std;

void print_vector(vector<int> nums)
{
    cout << "[" << nums[0];
    for (int i = 1; i < nums.size(); ++i) {
        cout << ", " << nums[i];
    }
    cout << "]" << endl;
}