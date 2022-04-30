#pragma once
#define REC_SIZE 8

#include "csv.h"
#include <string>
#include <vector>
#include <deque>
#include <algorithm>
#include <map>
#include <unordered_map>
#include <array>
#include <memory>
#include <iostream>
#include <fstream>
#include <set>

class Item {
public:
	Item(uint64_t id, long double price) : id_(id), price_(price) {
	}

	uint64_t GetID() {
		return id_;
	}
	long double GetPrice() {
		return price_;
	}
	void SetPrice(long double price) {
		price_ = price;
	}
	
private:
	uint64_t id_;
	long double price_ = 0;
};

class User {
public:
	User(uint64_t id) : id_(id) {
	}
	uint64_t GetID() {
		return id_;
	}
	void Recommend(const std::array<uint64_t, REC_SIZE>& new_recs, std::unordered_map<uint64_t, Item*>& itemid_to_item_);
	void SetPreferences(std::unordered_map<uint64_t, std::array<long double, REC_SIZE>>& new_prefs, std::unordered_map<uint64_t, Item*>& itemid_to_item_);
	const std::array<Item*, REC_SIZE>& GetRecommendations();
	std::unordered_map<Item*, std::array<long double, REC_SIZE>>& GetPreferences();
	long double Buy(Item& item, long double quantity = 1);
private:
	uint64_t id_;
	std::array<Item*, REC_SIZE> recommendations_;
	std::unordered_map<Item*, std::array<long double, REC_SIZE>> preferences_;
};

class Simulation {
public:
	Simulation(std::string items_file, std::string preference_file) : items_file_(items_file), preference_file_(preference_file) {
	}
	void CreateUsers();
	void SetRecFile(std::string rec_file);
	void ReadItems();
	void ReadRecs();
	void ReadPrefs();
	void Prepare();
	long double Execute(std::string rec_file);
private:
	std::string items_file_;
	std::string preference_file_;
	std::string rec_file_;
	std::vector<std::unique_ptr<User>> users_;
	std::vector<std::unique_ptr<Item>> items_;
	std::unordered_map<uint64_t, Item*> itemid_to_item_;
	std::unordered_map<uint64_t, User*> userid_to_user_;
	std::map<uint64_t, std::array<uint64_t, REC_SIZE>> i_recommendations_;
	std::unordered_map<uint64_t, std::unordered_map<uint64_t, std::array<long double, REC_SIZE>>> i_preferences_;
};
