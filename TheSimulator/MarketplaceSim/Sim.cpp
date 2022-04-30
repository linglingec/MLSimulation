#include "Sim.h"

void User::Recommend(const std::array<uint64_t, REC_SIZE>& new_recs, std::unordered_map<uint64_t, Item*>& itemid_to_item_) {
	for (size_t i = 0; i < REC_SIZE; ++i) {
		recommendations_[i] = itemid_to_item_[new_recs[i]];
	}
}

void User::SetPreferences(std::unordered_map<uint64_t, std::array<long double, REC_SIZE>>& new_prefs, std::unordered_map<uint64_t, Item*>& itemid_to_item_) {
	for (auto& [key, val] : new_prefs) {
		preferences_[itemid_to_item_[key]] = std::move(val);
	}
}

const std::array<Item*, REC_SIZE>& User::GetRecommendations() {
	return recommendations_;
}

std::unordered_map<Item*, std::array<long double, REC_SIZE>>& User::GetPreferences() {
	return preferences_;
}

long double User::Buy(Item& item, long double quantity) {
	long double spent = 0;
	spent += (item.GetPrice() * quantity);
	return spent;
}

void Simulation::CreateUsers() {
	std::set<uint64_t> visited;
	for (auto const& [key, val] : i_preferences_) {
		if (visited.find(key) == visited.end()) {
			users_.emplace_back(std::make_unique<User>(key));
			userid_to_user_[key] = users_.back().get();
			visited.insert(key);
		}
	}
}

void Simulation::SetRecFile(std::string rec_file) {
	rec_file_ = rec_file;
}

void Simulation::ReadItems() {
	io::CSVReader<2> in(items_file_);
	uint64_t item_id;
	long double item_price;
	while (in.read_row(item_id, item_price)) {
		items_.emplace_back(std::make_unique<Item>(item_id, item_price));
		itemid_to_item_[item_id] = items_.back().get();
	}
}

void Simulation::ReadRecs() {
	io::CSVReader<1 + REC_SIZE> in(rec_file_);
	uint64_t user_id;
	std::array<uint64_t, REC_SIZE> item_ids;
	while (in.read_row(user_id, item_ids[0], item_ids[1], item_ids[2], item_ids[3], item_ids[4], item_ids[5], item_ids[6], item_ids[7])) {
		i_recommendations_[user_id] = std::move(item_ids);
	}
}

void Simulation::ReadPrefs() {
	io::CSVReader<2 + REC_SIZE> in(preference_file_);
	uint64_t user_id;
	uint64_t item_id;
	std::array<long double, REC_SIZE> item_quantities;
	while (in.read_row(user_id, item_id, item_quantities[0], item_quantities[1], item_quantities[2], item_quantities[3], item_quantities[4], item_quantities[5], item_quantities[6], item_quantities[7])) {
		i_preferences_[user_id][item_id] = std::move(item_quantities);
	}
}

void Simulation::Prepare() {
	ReadItems();
	ReadPrefs();
	CreateUsers();
	for (auto const& user : users_) {
		user->SetPreferences(i_preferences_[user->GetID()], itemid_to_item_);
	}
}

long double Simulation::Execute(std::string rec_file) {
	long double revenue = 0;
	SetRecFile(rec_file);
	ReadRecs();
	for (auto const& user : users_) {
		auto& c_prefs = user->GetPreferences();
		auto& c_recs = user->GetRecommendations();
		user->Recommend(i_recommendations_[user->GetID()], itemid_to_item_);
		for (size_t i = 0; i < REC_SIZE; ++i) {
			if (c_recs[i]) {
				revenue += user->Buy(*c_recs[i], c_prefs[c_recs[i]][i]);
			}
		}
	}
	return revenue;
}


int main() {
	std::string items = "items.csv";
	std::string prefs = "prefs.csv";
	std::string recs = "recs.csv";
	std::string out_file = "out.txt";
	std::ofstream outfile;
	outfile.open(out_file);
	Simulation sim(items, prefs);
	sim.Prepare();
	int keep_going;
	std::cin >> keep_going;
	while (keep_going) {
		auto revenue = sim.Execute(recs);
		outfile << revenue << std::endl;
		std::cin >> keep_going;
	}
}
